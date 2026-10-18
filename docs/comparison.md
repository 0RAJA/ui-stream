# 如何选型：ui-stream 与相关方案

[English](comparison.en.md) · [文档导航](README.md)

核查日：2026-10-18。ui-stream 按本仓库 `0.1.0a1` 候选契约核对；其他方案依据下文官方资料。这是职责与接入成本比较，不是性能排名；未安装其他依赖，也未运行其他框架的网络压测。

## 为什么考虑 ui-stream

当后台任务已经由宿主调度、业务结果已经有自己的存储事务，而 UI 只需要当前快照和尽力交付的实时进度时，可以考虑 ui-stream。它把事件构造与编码、实时发布、归档提交拆成三个端口，在同步 Python 代码中复用缓冲、checkpoint、完整性状态和结束顺序；核心没有第三方运行时依赖，Pydantic 和 Redis 是可选 profile。

它提供的具体契约是：`finish()` 先调用宿主 `ArchiveStore.commit()`；提交成功后才关闭 runtime、尝试实时终态和调用 `after_close`。提交异常向外传播，并保留未结束状态。宿主必须在 `commit()` 内真正完成尾部、终态和业务结果的原子提交，并执行必要的 fencing。库本身不会把两个独立数据库或一次消息发布自动变成同一事务。

这个选择的代价是接入工作：宿主需要实现端口、授权、读取接口、事务和任务生命周期。同步端口调用可能阻塞，ASGI 接入需要由宿主安排线程或其他适配。现成 reader/SSE 辅助针对 Redis profile，并不是任意自定义通道的通用读取层。`StreamPolicy` 约束 runtime 的缓冲与发送/归档策略，HTTP 队列、客户端积压及存储保留仍需要各适配器管理。详见[架构](architecture.md)、[API](api.md)和[源码契约](../src/ui_stream/types.py)。


`max_events` 是累计归档预算：超过后快照可能冻结而 live/result 继续。它适合有预算的有限执行；无限期更新 latest value 的服务应先重新选择策略，详见 [API 边界](api.md)。

## 按需求选择

| 方案 | 主要提供什么 | 什么时候优先考虑 | 应用仍要处理什么 |
| --- | --- | --- | --- |
| **ui-stream** | 三个同步端口；有界 pending 缓冲、checkpoint、完整性状态；宿主提交成功后才尝试结束通知 | 已有后台执行与业务事务，需要为 UI 增加快照/实时进度，并复用这套提交与降级约定 | 真实事务、执行隔离/fencing、授权、HTTP、存储读取和重试；当前快照与实时尾部不承诺完整历史、无损重连或恢复计算 |
| **Starlette / FastAPI `StreamingResponse`** | 从同步/异步迭代器发送响应体；属于 HTTP 传输原语。[Starlette 官方文档](https://github.com/Kludex/starlette/blob/main/docs/responses.md#streamingresponse) | 只需要把现有迭代器发给客户端，或应用已经具有所需状态、存储及重连规则 | 此响应类自身不定义 SSE 事件、业务结果提交、存储与历史恢复；这些由调用方组合。若需要 SSE，可选下一行方案或当前 FastAPI 内置 SSE |
| **sse-starlette** | `EventSourceResponse`、SSE 事件编码、ping、断连检测和 `send_timeout`。[官方用法](https://github.com/sysid/sse-starlette#server-sent-events-for-starlette-and-fastapi) | Starlette/FastAPI 应用需要现成的 ASGI SSE 连接处理 | 应用定义事件来源、重连游标的数据来源、持久化及结果事务；仅设置 SSE `id` 不会自动建立历史存储。可作为 ui-stream 的传输层，需自行适配 |
| **FastAPI 内置 SSE** | 自 `0.135.0` 提供 `fastapi.sse.EventSourceResponse` / `ServerSentEvent`；支持类型化数据、idle ping 和响应头默认值。[官方 SSE 教程](https://fastapi.tiangolo.com/tutorial/server-sent-events/) | 已使用支持此功能的 FastAPI，需要由端点直接流式输出模型或 SSE 事件 | 官方示例让应用读取 `Last-Event-ID` 并选择从哪里继续；数据留存、业务事务和恢复策略仍由应用定义。仅为获得 SSE 编码/心跳，无需引入 ui-stream |
| **Django EventStream** | Django SSE views、channel 授权扩展点；启用事件存储后先写库再发布，默认保留 24 小时，支持断线补发。[官方存储与客户端说明](https://github.com/fanout/django-eventstream#event-storage) | Django 应用需要在保留窗口内补回中间事件，而不只是重新取得当前 UI 状态 | 存储默认关闭；多进程发布需 Redis 或 GRIP 等部署配置。超出保留范围时客户端要处理重置；业务结果的事务与执行生命周期仍需设计 |
| **LangGraph** | 图/agent 执行、按 thread 的 checkpoint、故障恢复、interrupt/resume，以及状态/token/自定义数据流。[Checkpoint 官方说明](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[流式输出](https://docs.langchain.com/oss/python/langgraph/streaming) | 需要恢复的是计算步骤、人工审批或 agent 状态，而不只是 UI 画面 | 要选择持久化 checkpointer 与 durability 模式；内存 checkpointer 不跨重启。[内存与持久化边界](https://docs.langchain.com/oss/python/langgraph/persistence)。interrupt 前的副作用需考虑节点重跑与幂等。[官方约束](https://docs.langchain.com/oss/python/langgraph/interrupts#side-effects-called-before-interrupt-must-be-idempotent)。外部业务结果与 UI 的事务衔接仍需显式集成 |

这些方案可以组合。比如，宿主任务调用 ui-stream 管理进度与最终提交，再用 ASGI SSE 响应发送事件；LangGraph 可以负责执行恢复，由应用把所需进度映射到 UI 事件。这里描述的是可行的职责组合，不表示仓库已提供或验证这些集成。若已有的 SSE、事件存储或 agent 流满足需求，ui-stream 可能不会减少工作量。

## 三种“恢复”要分清

- **恢复 UI 当前状态：**读取最新快照和业务结果，再接实时尾部。适合接受中间进度不完整的展示；这是 ui-stream 的目标用法，实际读取与衔接由宿主实现。
- **补回遗漏事件：**需要带保留策略的事件存储与游标恢复协议。Django EventStream 的存储模式更接近这一需求；ui-stream 的默认快照/实时模式不提供无损历史保证。
- **恢复未完成计算：**需要执行状态、checkpoint 和重新调度。LangGraph 等执行框架负责这层；ui-stream 的 `finish_retry()` 只 checkpoint 并关闭当前 runtime，不会重新执行任务。

不要把上述恢复能力等同于端到端 exactly-once。ui-stream 不自动去重；SSE event ID、本地锁和宿主 fencing 各有边界。

## 本次核查的官方版本来源

| 方案 | 核查来源 |
| --- | --- |
| sse-starlette | [v3.5.0 release](https://github.com/sysid/sse-starlette/releases/tag/v3.5.0)、[main 的 VERSION=3.5.0](https://raw.githubusercontent.com/sysid/sse-starlette/main/VERSION) |
| Starlette | [1.8.0 release](https://github.com/Kludex/starlette/releases/tag/1.8.0)；`StreamingResponse` 按官方 main 文档核对 |
| FastAPI | [master 的 `__version__=0.143.1`](https://raw.githubusercontent.com/fastapi/fastapi/master/fastapi/__init__.py)；[0.135.0 release notes](https://fastapi.tiangolo.com/release-notes/#01350) 记录内置 SSE 的引入 |
| Django EventStream | [master 的 `pyproject.toml` version=5.3.4](https://raw.githubusercontent.com/fanout/django-eventstream/master/pyproject.toml)；这是源码 metadata 核查，未声称做过该版本部署测试 |
| LangGraph | [1.2.16 release](https://github.com/langchain-ai/langgraph/releases/tag/1.2.16)、[main metadata](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/pyproject.toml)；核查时旧 `durable-execution` URL 重定向到 `persistence`，细节按当前 checkpointers 文档核对 |

这里的版本只标识核查对象，不表示性能报告运行了这些版本。现有性能报告中的 `sse-starlette 3.2.0` 仅用于 encoder 测量；该结果不能推出 ASGI 连接容量、恢复能力或上述方案的整体性能次序。
