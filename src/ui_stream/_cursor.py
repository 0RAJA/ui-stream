# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""校验默认 Redis profile 的游标，不触发 reader 或 JSON 模型导入。

reader 与默认 SSE 编码共用这一规则，合法字符串保持原表示。
身份选择、权限和游标是否仍可读取由宿主及后端判断。
"""

import re

from ui_stream.exceptions import InvalidCursor

STREAM_CURSOR_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")
STREAM_ID_PART_MAX = str(2**64 - 1)
STREAM_START_CURSOR = "0-0"


def normalize_cursor(value):
    """校验客户端续传游标，返回可用于 Redis 增量读取的原字符串。

    Args:
        value: 客户端上次收到的 stream_id；空值或其他假值视为从 0-0 开始。

    Returns:
        合法游标原样返回，包括前导零；非空输入必须是两个 ASCII 十进制 uint64 分量。

    Raises:
        InvalidCursor: 类型、格式或数值范围不合法；此处不检查事件是否存在或调用者权限。
    """

    if not value:
        return STREAM_START_CURSOR
    if not isinstance(value, str) or not STREAM_CURSOR_PATTERN.fullmatch(value):
        raise InvalidCursor("Invalid stream cursor")
    for part in value.split("-"):
        digits = part.lstrip("0") or "0"
        if len(digits) > len(STREAM_ID_PART_MAX) or (
            len(digits) == len(STREAM_ID_PART_MAX) and digits > STREAM_ID_PART_MAX
        ):
            raise InvalidCursor("Invalid stream cursor")
    return value
