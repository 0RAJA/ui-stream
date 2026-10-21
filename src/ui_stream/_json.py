# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""提供 JSON profile 与 SSE 共用的严格 UTF-8 数据编码。

保持标准库依赖，使自定义 SSE encoder 不必导入默认 Pydantic 事件模型。
这里只转换字节，事件构造、身份和 I/O 仍由对应 profile 与宿主负责。
"""

import json
from typing import Any

from ui_stream.exceptions import InvalidEvent


def serialize_data(data: Any) -> bytes:
    """编码标准 JSON 为紧凑 UTF-8；不可编码值或非有限浮点数抛 InvalidEvent。"""

    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise InvalidEvent("Stream data must be standard JSON") from None
