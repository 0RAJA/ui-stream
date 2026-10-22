"""State reconciliation must preserve live order and stop stale or unauthorized tails."""

import unittest

from ui_stream.codec import ControlEvent, Event
from ui_stream.exceptions import LiveUnavailable
from ui_stream.reader import iter_live
from ui_stream.types import ReadBatch


class ControlledLiveStore:
    """Represent completed socket reads without network I/O or wall-clock sleeps."""

    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.now = 0.0
        self.blocks = []
        self.cursors = []

    def read(self, *, redis_key, after_id, block_ms):
        self.blocks.append(block_ms)
        self.cursors.append(after_id)
        elapsed, result = next(self.rounds)
        self.now += elapsed
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ReadBatch):
            return result
        return ReadBatch(events=result, last_seen_stream_id=result[-1].stream_id if result else None)


def progress(text, cursor):
    return Event(stream_id=cursor, data={"content": text})


def terminal(cursor=None, status="SUCCESS"):
    return Event(event=ControlEvent.STREAM_END, stream_id=cursor, data={"status": status})


class ReaderStateTest(unittest.TestCase):
    def consume(self, rounds, state_check=lambda: None, **options):
        store = ControlledLiveStore(rounds)
        iterator = iter_live(
            redis_store=store,
            redis_key="synthetic:stream",
            cursor="0-0",
            state_check=state_check,
            state_poll_interval=1,
            clock=lambda: store.now,
            **options,
        )
        return store, iterator

    def test_initial_reset_is_checked_before_any_live_read(self):
        reset = Event(event=ControlEvent.STREAM_RESET, data={"reason": "execution_replaced"})
        checks = []

        def check():
            checks.append(True)
            return reset

        store, iterator = self.consume([(0, [progress("old", "1-0")])], state_check=check)
        self.assertEqual(checks, [])
        self.assertEqual(next(iterator), reset)
        self.assertEqual(list(iterator), [])
        self.assertEqual(store.cursors, [])
        self.assertEqual(checks, [True])

    def test_initial_state_failure_propagates_without_exposing_old_data(self):
        for error in (PermissionError("revoked"), LiveUnavailable(), OSError("state")):
            with self.subTest(error=type(error)):

                def check():
                    raise error

                store, iterator = self.consume([(0, [progress("old", "1-0")])], state_check=check)
                with self.assertRaises(type(error)):
                    next(iterator)
                self.assertEqual(store.cursors, [])

    def test_initial_terminal_drains_dirty_entries_then_valid_tail(self):
        end = terminal()
        last = progress("tail", "2-0")
        store, iterator = self.consume([(0.1, ReadBatch([], "1-0")), (0.1, [last]), (0.1, [])], state_check=lambda: end)
        self.assertEqual(next(iterator), last)
        self.assertEqual(list(iterator), [end])
        self.assertEqual(store.blocks, [0, 0, 0])
        self.assertEqual(store.cursors, ["0-0", "1-0", "2-0"])

    def test_normal_batches_keep_order_and_identical_payloads(self):
        events = [progress("A", "1-0"), progress("A", "2-0"), progress("B", "3-0"), terminal("4-0")]
        store, iterator = self.consume([(0.1, events[:2]), (0.1, events[2:])])

        self.assertEqual(list(iterator), events)
        self.assertEqual(store.cursors, ["0-0", "2-0"])

    def test_lost_terminal_uses_durable_state_after_a_raw_empty_read(self):
        first = progress("A", "1-0")
        end = terminal()
        store, iterator = self.consume([(0.1, [first]), (1, [])], state_check=lambda: end)

        self.assertEqual(list(iterator), [first, end])
        self.assertIsNone(end.stream_id)
        self.assertEqual(store.cursors, ["0-0", "1-0"])

    def test_durable_terminal_drains_queued_progress_before_synthetic_end(self):
        first, second = progress("A", "1-0"), progress("B", "2-0")
        end = terminal(status="FAILED")
        store, iterator = self.consume(
            [(0.1, [first]), (1, [second]), (0.1, [])], state_check=lambda: end, idle_timeout=0.5
        )

        self.assertEqual(list(iterator), [first, second, end])
        self.assertEqual(store.blocks[-1], 0)

    def test_queued_redis_end_keeps_its_cursor_and_payload(self):
        first, second = progress("A", "1-0"), progress("B", "2-0")
        redis_end = terminal("3-0")
        store, iterator = self.consume(
            [(0.1, [first]), (1, [second, redis_end])], state_check=lambda: terminal(status="FAILED")
        )

        self.assertEqual(list(iterator), [first, second, redis_end])
        self.assertEqual(store.cursors, ["0-0", "1-0"])

    def test_terminal_check_mid_batch_or_before_read_preserves_all_200_events(self):
        for pause_after in (50, 100):
            with self.subTest(pause_after=pause_after):
                business = [progress(str(index), f"{index}-0") for index in range(1, 201)]
                end = terminal("201-0")
                store, iterator = self.consume(
                    [(0.1, business[:100]), (0.1, business[100:]), (0.1, [end])],
                    state_check=terminal,
                )
                received = [next(iterator) for _ in range(pause_after)]
                store.now += 1
                received.extend(iterator)

                self.assertEqual(received, [*business, end])
                self.assertEqual(store.cursors, ["0-0", "100-0", "200-0"])
                self.assertEqual(store.blocks[1:], [0, 0])

    def test_dirty_only_batch_does_not_end_terminal_drain(self):
        first, last = progress("A", "1-0"), progress("B", "3-0")
        end = terminal()
        store, iterator = self.consume(
            [(0.1, [first]), (1, ReadBatch([], "2-0")), (0.1, [last]), (0.1, [])],
            state_check=lambda: end,
        )

        self.assertEqual(list(iterator), [first, last, end])
        self.assertEqual(store.cursors, ["0-0", "1-0", "2-0", "3-0"])
        self.assertEqual(store.blocks[2:], [0, 0])

    def test_reset_during_batch_discards_remaining_old_generation_events(self):
        for pause_after in (1, 2):
            with self.subTest(pause_after=pause_after):
                old = [progress("old", "1-0"), progress("old", "2-0")]
                reset = Event(event=ControlEvent.STREAM_RESET, data={"reason": "execution_replaced"})
                states = iter((None, reset))
                store, iterator = self.consume([(0.1, old)], state_check=lambda: next(states))
                received = [next(iterator) for _ in range(pause_after)]
                store.now += 1
                received.extend(iterator)

                self.assertEqual(received, [*old[:pause_after], reset])
                self.assertEqual(store.cursors, ["0-0"])

    def test_state_callback_errors_propagate_before_queued_data_is_exposed(self):
        for error in (
            PermissionError("revoked"),
            OSError("state unavailable"),
            LiveUnavailable(),
            RuntimeError("state"),
        ):
            with self.subTest(error=type(error)):

                states = iter((None, error))

                def check():
                    state = next(states)
                    if isinstance(state, Exception):
                        raise state
                    return state

                first = progress("allowed", "1-0")
                store, iterator = self.consume([(0.1, [first]), (1, [progress("forbidden", "2-0")])], check)
                self.assertEqual(next(iterator), first)
                with self.assertRaises(type(error)):
                    next(iterator)
                self.assertEqual(store.cursors, ["0-0", "1-0"])

    def test_terminal_drain_still_checks_for_execution_replacement_or_revoked_access(self):
        reset = Event(event=ControlEvent.STREAM_RESET, data={"reason": "execution_replaced"})
        for changed_state in (reset, PermissionError("revoked")):
            with self.subTest(changed_state=changed_state):
                states = iter((None, terminal(), changed_state))

                def check():
                    state = next(states)
                    if isinstance(state, Exception):
                        raise state
                    return state

                first, second = progress("A", "1-0"), progress("B", "2-0")
                store, iterator = self.consume([(0.1, [first]), (0.1, [second])], state_check=check)
                self.assertEqual(next(iterator), first)
                store.now += 1
                self.assertEqual(next(iterator), second)
                store.now += 1
                if isinstance(changed_state, Exception):
                    with self.assertRaises(PermissionError):
                        next(iterator)
                else:
                    self.assertEqual(list(iterator), [reset])
                self.assertEqual(store.blocks, [1000, 0])

    def test_live_failure_during_terminal_drain_does_not_guess_that_tail_is_empty(self):
        first = progress("A", "1-0")
        store, iterator = self.consume([(0.1, [first]), (0.1, LiveUnavailable())], state_check=terminal)
        self.assertEqual(next(iterator), first)
        store.now += 1

        self.assertEqual(list(iterator), [])
        self.assertEqual(store.blocks, [0, 0])

    def test_live_failure_disconnects_without_synthesizing_a_terminal(self):
        first = progress("A", "1-0")
        _, iterator = self.consume([(0.1, [first]), (1, LiveUnavailable())], state_check=terminal)

        self.assertEqual(list(iterator), [first])

    def test_socket_block_is_capped_by_remaining_poll_budget_and_host_limit(self):
        first = progress("A", "1-0")
        store, iterator = self.consume([(0.9, [first]), (0.1, [terminal("2-0")])], block_ms=250)

        list(iterator)

        self.assertEqual(store.blocks[0], 250)
        self.assertGreaterEqual(store.blocks[1], 1)
        self.assertLessEqual(store.blocks[1], 101)

    def test_explicit_nonblocking_reads_remain_nonblocking(self):
        store, iterator = self.consume([(0.1, [progress("A", "1-0")]), (0.1, [terminal("2-0")])], block_ms=0)

        list(iterator)

        self.assertEqual(store.blocks, [0, 0])

    def test_dirty_entries_and_heartbeats_do_not_extend_business_idle_timeout(self):
        first = progress("A", "1-0")
        store, iterator = self.consume([(0.1, [first]), (1, ReadBatch([], "2-0")), (1, [])], idle_timeout=1.5)

        self.assertEqual(list(iterator), [first, None])
        self.assertEqual(store.cursors, ["0-0", "1-0", "2-0"])

    def test_redis_closing_event_does_not_expose_later_entries(self):
        for closing in (terminal("2-0"), Event(event=ControlEvent.STREAM_RESET, stream_id="2-0")):
            with self.subTest(closing=closing.event):
                first = progress("A", "1-0")
                _, iterator = self.consume([(0.1, [first, closing, progress("hidden", "3-0")])])
                self.assertEqual(list(iterator), [first, closing])

    def test_state_interval_must_be_positive_when_callback_is_enabled(self):
        for interval in (0, -1):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                list(
                    iter_live(
                        redis_store=None,
                        redis_key="key",
                        cursor="0-0",
                        state_check=lambda: None,
                        state_poll_interval=interval,
                    )
                )

    def test_without_callback_keeps_one_clock_read_per_batch(self):
        ticks = iter((0, 0.2, 0.4))
        store = ControlledLiveStore([(0, ReadBatch([], "1-0")), (0, [terminal("2-0")])])
        iterator = iter_live(
            redis_store=store, redis_key="key", cursor="0-0", clock=lambda: next(ticks), state_poll_interval=0
        )

        self.assertEqual(list(iterator), [None, terminal("2-0")])
        self.assertEqual(store.cursors, ["0-0", "1-0"])
        self.assertEqual(list(ticks), [])
