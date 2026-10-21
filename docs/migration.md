# 从 a2 迁移到 a3

[English](migration.en.md) · [文档导航](README.md)

从 `0.1.0a2` 升级到 `0.1.0a3` 时，需要更新自定义 codec，并检查旧流通知、归档追加和 SSE 生命周期的接入方式。升级前固定版本、更新适配器并运行契约测试。

## Codec 必须实现 reset

`EventCodec` 统一要求 `business(data)`、`terminal(status)`、`reset(reason)`、`encode(event, target)`。自定义 codec 必须补上 reset；Runtime 不探测旧协议。默认 `JsonEventCodec.reset(reason)` 构造无游标的 `STREAM_RESET`，数据为 `{"reason": reason}`。构造/编码错误直接传播。已有终态请复用 `codec.terminal(status)`。

[Counter](../examples/counter.py) 和 [DemoCodec](../demos/sqlite/events.py) 展示自定义非默认协议；a2 的固定/动态 policy 规则保持不变。

## 轮换执行后通知旧通道

宿主先提交新 execution 的持久轮换和旧执行写入隔离，再在新 Runtime 调用 `announce_replacement(previous_publisher)`，传旧执行的 publisher。下面仅展示内存端口调用；数据库事务与 execution fencing 由宿主实现。

```python
from examples.counter import make_counter

previous, old_channel, old_store = make_counter()
runtime, channel, store = make_counter()
assert runtime.announce_replacement(old_channel)
assert old_channel.frames == [b"reset:execution_replaced"]
runtime.send(4)
runtime.finish(status="DONE", completion=4)
assert store.snapshot.result == 4
```

新 Runtime 的 codec 构造 `reset("execution_replaced")` 并校验 LIVE 编码。只有旧 publisher 发布时的 `live_errors` 被降级：返回 False，标记新 Runtime 的 DEGRADED，并记录身份及异常类型，不记录正文。其他错误传播；True 仅表示 publish 正常返回。此调用不累计预算、不归档、不关闭执行或自动重试。未收到通知的读者仍需宿主状态复核兜底。两端必须使用匹配的事件协议。

## 归档追加按宿主身份去重

```python
from ui_stream.archive import append_archive_events
from ui_stream.codec import Event

first = Event(stream_id="1-0", data="A")
second = Event(stream_id="2-0", data="B")
result = append_archive_events(
    [first], [first, second], max_bytes=1024, identity=lambda event: event.stream_id
)
assert result.events == [first, second]
assert (result.appended_count, result.duplicate_count, result.dropped_count) == (1, 1, 0)
assert not result.truncated
```

`append_archive_events` 接受有限序列，先按 `identity(event)` 去重，再复用 `fit_archive_events` 保留容量能容纳的连续前缀。已有事件原样保留，重复 ID 首条优先，只有 None 表示无身份；无身份事件全部保留候选资格，不按正文去重。示例使用真实回执形状；生产中的重试身份必须由宿主持久保存且不可变，Runtime 不生成 ID。

结果包含新列表 `events`、本批新增 `appended_count`、重复 `duplicate_count`、容量丢弃 `dropped_count`；三项之和等于 incoming 长度，`truncated` 表示容量丢弃。去重先于容量检查，因此被容量拒绝的首条记录之后，相同 ID 仍计重复。已有超大数据不会被清洗；容量拒绝第一条候选后不跳过它接收后面的更小事件。宿主负责事务和以实际存储表示计量 `event_size`，尤其是公开序列化隐藏的内部字段。

## HTTP 层直接持有同步 SSEBody

```python
from ui_stream.sse import SSEBody, encode_sse_heartbeat


def encode_update(value):
    return encode_sse_heartbeat() if value is None else f"data: {value}\n\n".encode()


body = SSEBody(iter([4, None]), encoder=encode_update)
try:
    assert list(body) == [b"data: 4\n\n", b": heartbeat\n\n"]
finally:
    body.close()
```

用 `SSEBody(events, encoder=...)` 替换只做编码的外层生成器。events 必须是同步 Iterator；只有 Iterable 时由宿主显式 iter。默认编码器接受默认 Event/None；自定义编码器接收每个源元素并返回 bytes。SSEBody 自身只依赖标准库，自定义协议无需安装 Pydantic。HTTP 框架应在断开和退出时调用 body.close。

body 在首次 next 前也能向源 iterator 的 close 透传；正常耗尽和读取/编码异常同样关闭，最多调用一次。它不拥有外层 Iterable、共享客户端、通道或生产任务。直接 close 的异常传播；处理已有读取/编码错误时，普通清理异常只记录类型并保留原错误。编码器或自然耗尽后的清理抛 StopIteration 会转为带 cause 的 RuntimeError，避免误报正常结束；显式 close 保留原异常。未启动的源生成器是否执行 finally 仍遵循 Python 规则；close 不能中断已阻塞的 next，不支持并发 next/close 或 async iterator。

完整默认 Redis 接入见 [API](api.md)，Demo 展示保留自定义整数游标的 [HTTP 边界](../demos/sqlite/app.py)。游标来源优先级与 HTTP 响应头仍由宿主决定。
