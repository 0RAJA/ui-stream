# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stable integration errors; backend adapters choose which failures are recoverable."""


class InvalidEvent(ValueError):
    pass


class InvalidCursor(ValueError):
    pass


class ClosedStream(RuntimeError):
    pass


class StaleExecution(RuntimeError):
    pass


class LiveUnavailable(RuntimeError):
    pass


class ArchiveUnavailable(RuntimeError):
    pass
