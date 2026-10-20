# Copyright (C) 2023 THL A29 Limited, a Tencent company. All rights reserved.
# SPDX-License-Identifier: MIT
"""提供稳定的接入错误与后端故障分类，供宿主适配异常边界。

业务发布/中间归档默认仅对配置的后端故障降级；参数与执行失效错误向上暴露。
最终提交失败传播；提交后的终态通知捕获 Exception，不能回滚结果。
适配器负责后端错误映射，核心不决定执行重试或权限策略。
"""


class InvalidEvent(ValueError):
    """事件不符合所选编码协议；调用方应修正输入，不能按后端短暂故障吞掉。"""

    pass


class InvalidCursor(ValueError):
    """续传游标不符合 Redis ID 格式或数值范围，由宿主转换为接口错误。"""

    pass


class ClosedStream(RuntimeError):
    """已 finish 或 finish_retry 的 Runtime 又收到写入/正常终止调用。"""

    pass


class StaleExecution(RuntimeError):
    """宿主 fencing 发现执行已过期；Runtime 默认不把它作为可恢复归档故障处理。"""

    pass


class LiveUnavailable(RuntimeError):
    """实时后端暂不可用；默认 Runtime 继续归档，reader 结束本次实时读取。"""

    pass


class ArchiveUnavailable(RuntimeError):
    """中间归档暂不可用；自动 checkpoint 可降级，但最终 commit 失败仍须传播。"""

    pass
