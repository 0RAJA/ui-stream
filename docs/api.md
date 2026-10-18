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
