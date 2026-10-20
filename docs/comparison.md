# 如何选型

[English](comparison.en.md) · [文档导航](README.md)

依据 2026-10-18 的官方资料与 ui-stream `0.1.0a1` 契约，比较职责和接入成本；性能测量另见[基线报告](performance.md)。

## 什么时候选 ui-stream

宿主已经调度后台任务、拥有业务结果事务，而 UI 需要最新快照和尽力交付的实时进度时，ui-stream 可复用缓冲、checkpoint、完整性跟踪和完成顺序。核心同步、无第三方依赖，Pydantic/Redis 为可选 profile。

`finish()` 先调用宿主 `ArchiveStore.commit()`；成功后才关闭 runtime、尝试实时终态和 `after_close`。提交异常传播并保持 runtime open。宿主必须真正原子提交尾部、终态和业务结果，并提供 fencing；独立数据库与消息发布不会自动合为同一事务。

接入前需接受这些成本与边界：

- 宿主实现端口、授权、读取、事务和任务生命周期；同步调用可能阻塞，ASGI 需安排线程或适配层。
- 现成 reader 按 Redis 游标读取，默认 SSE 编码器使用默认 Event 和 Redis 格式 ID；其他协议可像 Demo 自行实现。HTTP 队列、客户端积压和存储保留由适配器管理。
- `max_events` 是累计归档预算，达到后快照可能停止而 live/result 继续。无限期 latest-value 服务需另选策略，见 [API](api.md)。

如果已有 SSE、事件存储或 agent 流满足需求，通常不必再引入这层协调。端口与完成顺序详见[架构](architecture.md)。

## 按需求选择

| 需求 | 优先考虑 | 应用仍需提供 |
| --- | --- | --- |
| 已有任务与事务，需要有界快照/进度协调 | **ui-stream**：三个同步端口、checkpoint、完整性状态、提交后通知 | 原子事务、执行 fencing、授权、HTTP、读取与重试；不提供完整历史、无损重连或计算恢复 |
| 将已有迭代器发送为 HTTP 响应 | **Starlette / FastAPI [`StreamingResponse`](https://github.com/Kludex/starlette/blob/main/docs/responses.md#streamingresponse)** | SSE 格式、存储、重连和业务结果提交 |
| Starlette/FastAPI 的 ASGI SSE 连接处理 | **[sse-starlette](https://github.com/sysid/sse-starlette#server-sent-events-for-starlette-and-fastapi)**：编码、ping、断连检测、`send_timeout` | 事件来源、历史、游标恢复和结果事务；SSE `id` 不等于历史存储 |
| 直接从 FastAPI 端点发送类型化 SSE | **[FastAPI 内置 SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)**：自 `0.135.0` 提供 `EventSourceResponse` / `ServerSentEvent`、idle ping 与默认响应头 | `Last-Event-ID` 之后的续接点、留存与事务；仅需编码/心跳时已足够 |
| Django 中补回保留窗口内的遗漏事件 | **[Django EventStream](https://github.com/fanout/django-eventstream#event-storage)**：授权扩展点、可选先存后发、默认保留 24 小时 | 显式启用存储；多进程配置 Redis/GRIP；超出保留期的 reset、结果事务与执行生命周期 |
| 恢复计算步骤、审批或 agent 状态 | **LangGraph**：[thread checkpoint](https://docs.langchain.com/oss/python/langgraph/checkpointers)、interrupt/resume 与[流式输出](https://docs.langchain.com/oss/python/langgraph/streaming) | 选择[持久化 checkpointer 与 durability 模式](https://docs.langchain.com/oss/python/langgraph/persistence)；内存模式不跨重启；[重跑副作用需幂等](https://docs.langchain.com/oss/python/langgraph/interrupts#side-effects-called-before-interrupt-must-be-idempotent)，外部结果/UI 事务需集成 |

这些层可以组合：例如 ui-stream 协调进度与提交，ASGI SSE 负责传输；LangGraph 恢复执行，应用映射 UI 事件。仓库未提供或验证这些组合。

## 三种恢复

- **恢复当前 UI**：读取最新快照/结果，再接实时尾部；允许遗漏中间进度。这是 ui-stream 的目标，读与衔接由宿主实现。
- **补回事件**：需要事件保留和游标恢复协议；Django EventStream 的存储模式更接近这一需求。
- **续跑计算**：需要执行状态、checkpoint 与重新调度；由 LangGraph 等执行框架处理。ui-stream 的 `finish_retry()` 仅 checkpoint 并关闭，不会重跑任务。

这些能力均不等于端到端 exactly-once；ui-stream 不自动去重，SSE ID、进程内锁与宿主 fencing 的作用范围不同。

## 版本来源

| 方案 | 官方版本来源 |
| --- | --- |
| sse-starlette | [v3.5.0 release](https://github.com/sysid/sse-starlette/releases/tag/v3.5.0) |
| Starlette | [1.8.0 release](https://github.com/Kludex/starlette/releases/tag/1.8.0)；响应行为按官方 main 文档 |
| FastAPI | [master `__version__=0.143.1`](https://raw.githubusercontent.com/fastapi/fastapi/master/fastapi/__init__.py)；[0.135.0 release notes](https://fastapi.tiangolo.com/release-notes/#01350) 记录 SSE 引入 |
| Django EventStream | [master metadata 5.3.4](https://raw.githubusercontent.com/fanout/django-eventstream/master/pyproject.toml) |
| LangGraph | [1.2.16 release](https://github.com/langchain-ai/langgraph/releases/tag/1.2.16) |

以上是文档核查对象，未进行部署测试或框架网络比较。性能基线中的 sse-starlette **3.2.0** 仅参与编码测量。
