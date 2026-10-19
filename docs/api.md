# API 契约

[English](api.en.md) · [文档导航](README.md)

alpha 阶段请固定精确版本 `0.1.0a1`；升级前跑自己的适配器契约。以下划线开头的名称不属于公开 API。
签名以 [types.py](../src/ui_stream/types.py) 和 [runtime.py](../src/ui_stream/runtime.py) 为准。

| 入口 | 方法 / 类型 |
| --- | --- |
| `StreamRuntime` | `send` / `finish` / `finish_retry` |
| `EventCodec` | `business` / `terminal` / `encode` |
| `LivePublisher` | `publish` |
| `ArchiveStore` | `checkpoint` / `touch_activity` / `commit` |
| `after_close` | `Summary` |

公开模块：`ui_stream`、`ui_stream.types`、`ui_stream.exceptions`；可选 profile 位于 `codec`、`redis`、`reader`、`sse`、`archive`。
默认 `JsonEventCodec` 使用 SUCCESS/FAILED，自定义 codec 可用其他终态与事件类型。

| `StreamPolicy` | 默认值 |
| --- | --- |
| `max_event_bytes` | 256 KiB |
| `max_events` | 10000 |
| `max_live_bytes` / `max_archive_bytes` | 各 10 MiB |
| `activity_interval_seconds` | 60 |
| `checkpoint_event_count` / `checkpoint_bytes` | 20 / 256 KiB |

超单条限制的 `send` 返回 None。事件数限制停止新的归档缓冲；live 字节限制停止新的业务实时发布，终态仍会尝试。
Store 自行限制持久快照容量；活动更新由 `send` 驱动，不是后台定时器。
`finish_retry` checkpoint 后关闭，不构造终态或提交最终结果。

宿主迁移：声明精确版本 extra 依赖，移除同名内嵌 `ui_stream` 防止遮蔽，重新生成依赖锁并验证实际导入位置；业务模型、权限、事务和任务异常留在宿主。

## 有限归档预算与真实完整性

`max_events` 是一次 execution 的累计归档事件预算，不是 pending 批大小。比如设为 2、每条 checkpoint，发送 1–4 后，latest snapshot 可停在 2，而实时值与最终业务结果继续到 4，完整性为 TRUNCATED。即使 Store 只保留常量空间的最新值，这个累计策略仍生效。有限任务应配置足够预算；长任务应显式轮换 execution 或选择适合无限最新值的策略/实现，不能将默认配置当作无限快照服务。

`Checkpoint(capacity_exhausted=True)` 表示后续停止归档缓冲。若本批恰好填满且没有丢事件，可以仍是 COMPLETE；下一条实际被丢弃才升级 TRUNCATED。`Summary` 的标志也包含构造时传入的初始完整性。

## 可选 Redis reader

`normalize_cursor` 接受 ASCII 的两段 uint64 Redis stream ID。reader 不负责授权、初始快照或重连补发。
使用 `decode_responses=False` 可让适配器逐项跳过坏 payload 并推进真实 entry ID。正常的字符串响应也受支持；但 `decode_responses=True` 的客户端可能在返回任何 entry 前因坏 UTF-8 解析失败，此时归一为 `LiveUnavailable` 并结束本次 reader，由宿主重新读快照；不会猜测未知游标或承诺跳过该 entry。

## Redis reader 的宿主状态与归档计量

`iter_live(..., state_check=None, state_poll_interval=15)` 可接收无参宿主检查，返回控制事件或 `None`。同 execution 结束提示会先排空已发布尾流；原结束事件和游标优先透传，raw 空批才合成结束。reset 立即关闭，宿主权限异常向外传播；检查和异常处理不引入模型依赖。默认不启用检查。

`encoded_archive_bytes(events, event_size=None)` 与 `fit_archive_events(..., event_size=None)` 共用单事件计量函数；默认计量 model_dump。内部字段被公开序列化排除时，宿主传入实际持久记录的字节计量。连续前缀裁剪算法仍只有一份。
