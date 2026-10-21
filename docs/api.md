# API：组装、调用与故障处理

[English](api.en.md) · [文档导航](README.md)

先阅读 [架构与概念](architecture.md)，再按下面的生命周期接入。签名以 [types.py](../src/ui_stream/types.py) 和 [runtime.py](../src/ui_stream/runtime.py) 为准，下划线开头的名称不是公开 API。

## 1. 创建 Runtime

宿主先创建一次 execution，把身份和三个端口传给 `StreamRuntime`。端口完整示例见 [Counter](../examples/counter.py)；需要持久事务和 UI 读取时看 [SQLite Demo](../demos/sqlite/README.md)。

| 构造参数 | 传什么、用来做什么 |
| --- | --- |
| `binding` | `Binding(resource_id, task_id, execution_id)`，或有相同只读属性的对象；每次存储调用都携带它，宿主据此校验执行身份。 |
| `codec` | 实现 `business`、`terminal`、`reset`、`encode`，决定输入数据、事件结构、终态词汇和字节表示。 |
| `publisher` | 实现同步 `publish(event)`，返回后续归档用的事件，可附加真实消息 ID 回执。 |
| `archive_store` | 实现 `checkpoint`、`touch_activity`、`commit`，保存进度与最终业务结果。 |
| `policy` / `archive_status` | 固定 StreamPolicy 或返回它的无参工厂，以及此次执行已有的归档状态；按次求值规则见下文。 |
| `live_errors` / `archive_errors` | 允许降级继续的异常类型。默认分别为 `(LiveUnavailable, OSError)` 与 `(ArchiveUnavailable,)`；最终 commit 不吞异常。 |
| `after_close` / `closed_error` | 可选关闭观察者；关闭后调用 `send`/`finish` 默认抛 `ClosedStream`，自定义异常须支持无参构造。 |

初始化只保存端口和配置，不创建持久 execution、不访问后端。宿主负责连接的创建/关闭；Runtime 没有统一关闭客户端的方法。

## 2. 发送进度并结束

| 方法 | 输入与输出 | 何时调用 |
| --- | --- | --- |
| `send(data)` | `data` 由 codec 解释；返回待归档事件，可能带发布回执；单条超大时返回 `None`。 | 任务产生进度时反复调用；可能同步触发 checkpoint。 |
| `finish(status=..., completion=..., error_code="")` | `status` 交给 codec 构造终态；`completion` 交给 store 保存最终结果；`error_code` 仅进入关闭摘要。返回 `None`。 | 任务计算结束后调用，先 commit 再关闭、通知。 |
| `finish_retry()` | 尝试 checkpoint 后关闭，不保存最终结果或构造终态；已关闭时重复调用直接返回。 | 宿主准备重试或退出本次运行时；它本身不重新调度任务。 |

默认 `JsonEventCodec.terminal` 只接受 `SUCCESS` / `FAILED`；Counter 使用 `DONE` / `FAILED`，Demo 使用自己的 `DONE` / `ERROR` / `INTERRUPTED`。这些属于 codec 的业务协议，与 `Integrity` 无关。

`send` 返回事件并不证明已发布或已归档：实时后端降级时仍可返回原事件，归档也可能还在 pending 中。`finish` 的终态编码或 commit 失败会保留 pending 和打开状态；宿主应判断失败原因与提交是否已发生，再选择重试。`finish_retry` 遇到配置内归档故障仍会关闭，因此不能把它当成持久保存成功的确认。

### 各阶段异常

| 阶段 | Runtime / reader 行为 |
| --- | --- |
| 构造/编码非法事件、非法游标 | `InvalidEvent` / `InvalidCursor` 等向调用方传播，应修正输入。 |
| 业务实时发布遇到 `live_errors` | 记 `DEGRADED`，继续归档；之后的事件还可尝试发布。 |
| 中间 checkpoint / 活动更新遇到 `archive_errors` | 记 `DEGRADED`；checkpoint 失败保留 pending，后续事件可触发恢复。 |
| 默认配置下的 `StaleExecution`、其他未配置端口异常 | 向宿主传播，不能把旧执行当作暂时网络故障继续写。 |
| 最终 `commit` 失败 | 始终传播，不关闭、不发实时终态、不通知观察者。事务与重试幂等由存储端实现。 |
| 提交后的终态发布 / `after_close` 失败 | 捕获 `Exception`，不改变已提交结果；终态发布失败会使内存状态降级，但不回写持久记录。 |

### 新执行通知旧流

`announce_replacement(previous_publisher) -> bool` 在新 Runtime 上构造并校验 `codec.reset("execution_replaced")`，向旧执行的 publisher 发布。宿主必须先提交 execution 轮换和写入隔离，两端使用匹配的 codec 协议。已关闭的 Runtime 先抛关闭异常。

只有 publish 阶段配置内的 `live_errors` 被捕获：记录新执行身份和异常类型、标记新 Runtime DEGRADED、返回 False。构造/编码错误及其他异常直接传播；True 表示 publish 正常返回，不证明读者收到。此方法不调用策略工厂，不影响事件预算/pending/归档，不关闭或重试。失败后的重试决策、读者状态复核与数据库事务由宿主负责。

## 3. 配置容量和归档批量

### 固定策略与按次读取配置

`policy=StreamPolicy(...)` 保持原有固定策略行为；也可以传入 `policy=current_policy`，其中工厂无参并返回一份 `StreamPolicy`。默认仍是固定对象，构造 Runtime 时不调用工厂。

工厂支持从 `0.1.0a2` 开始提供；`0.1.0a1` 仅支持固定策略。需要动态配置的宿主应固定 `0.1.0a2` 或更新版本，并验证适配器契约。

每次 `send` 在实例锁内先确认未关闭，再调用工厂一次；当前事件的大小、实时预算、归档缓冲、checkpoint 和活动刷新都使用这份策略。配置变化从下一次 `send` 生效，已有 pending 会按新阈值检查。例如：

```python
from examples.counter import make_counter
from ui_stream import StreamPolicy

limits = {"checkpoint_event_count": 2}


def current_policy():
    return StreamPolicy(**limits)


runtime, channel, store = make_counter(policy=current_policy)
runtime.send(4)
assert store.snapshot.latest is None
limits["checkpoint_event_count"] = 1
runtime.send(9)
assert store.snapshot.latest == 9
runtime.finish(status="DONE", completion=13)
```

实际宿主可在工厂内读取自己的配置并构造策略，例如 Django settings；库不导入 Django。工厂只计算或读取配置，应轻量、无后端 I/O，也不要调用同一 Runtime 的 `send` / `finish` / `finish_retry`。工厂自身异常直接传播，返回非 `StreamPolicy` 抛 `TypeError`，均发生在业务事件处理、计数和外部写入之前；不会作为实时/归档故障降级。

`finish` / `finish_retry` 不使用策略阈值，不额外调用工厂；关闭后 `send` 先抛 `ClosedStream`，也不调用工厂。`runtime.policy` 仍是只读属性：固定值原样返回，工厂模式每次外部读取都会重新求值。不要依赖多次属性读取取得同一份动态配置。

更新策略不清零累计事件/字节，也不重新启动已经停止的实时发布或归档缓冲；已记录的完整性不会恢复。它改变的是后续判断使用的阈值，不会重建执行状态或自动启动定时器。

### 容量与批量阈值

以下限制针对业务进度，终态另走 commit 和实时通知。字节来自 codec 编码，不是原始 `data` 大小。

| `StreamPolicy` 字段 | 默认值 | 达到或超过阈值时发生什么 |
| --- | --- | --- |
| `max_event_bytes` | 256 KiB | 单条 LIVE 编码**超过**此值，拒绝该事件，`send` 返回 None，记 TRUNCATED。 |
| `max_events` | 10000 | 前 10000 条通过单条大小检查的业务事件计入预算；再来新事件时停止归档缓冲、记 TRUNCATED，实时发布仍可继续。 |
| `max_live_bytes` | 10 MiB | 累计成功发布的业务 LIVE 字节将超预算时，永久停止本实例的业务实时发布、记 DEGRADED。 |
| `max_archive_bytes` | 10 MiB | 限制 pending 的 ARCHIVE 编码字节和；溢出前先尝试归档已有缓冲，仍放不下则丢弃新事件并停止后续缓冲、记 TRUNCATED。不含持久存储的数组/信封开销。 |
| `checkpoint_event_count` / `checkpoint_bytes` | 20 / 256 KiB | pending 条数或 ARCHIVE 字节达到阈值，同步尝试 checkpoint。 |
| `activity_interval_seconds` | 60 秒 | 新事件到达时检查间隔：正常归档触发 checkpoint，停止归档后调用 `touch_activity`；不是后台定时心跳。 |

`max_events` 是一次执行的累计预算，不会在 checkpoint 后清零。例如预算为 2、每条都归档，发送 1–4 后快照可停在 2，而实时进度与最终业务结果继续到 4。即使 store 只保存常量空间的最新值，也受这个策略限制。长任务应明确选择预算或执行轮换方案；没有内置“无限最新值”开关。

`StreamPolicy` 本身不验证配置。持久快照的**完整大小**由 store 另外限额；不能把 Runtime 的 pending 限制当作数据库归档上限。

## 4. 理解完整性、回执和关闭摘要

虽然属性叫 `archive_status`，实时通道故障也会记录在这里，以提示 UI 进度可能缺失：

| `Integrity` | 含义 |
| --- | --- |
| `COMPLETE` | 尚未发现实时/归档故障或已知丢弃；不是永久保存完整历史的承诺。 |
| `DEGRADED` | 曾遇到可恢复后端故障，或实时预算使发布停止；任务仍可能成功。 |
| `TRUNCATED` | 已知有进度事件因单条大小、累计条数、缓冲或存储容量被丢弃。 |

用 `merge_archive_status(current, incoming)` 保留更严重的状态；后续成功不清除曾发生的问题。存储端 checkpoint 返回 `Checkpoint(archive_status, capacity_exhausted)`：整批已处理才能返回；若容量恰好用尽但没丢事件，可以仍为 COMPLETE，下一条被丢弃时才升 TRUNCATED。

关闭后 `after_close(summary)` 在锁外调用一次。`Summary.status` 是 finish 的业务状态或 `RETRY`；`degraded` / `truncated` 分别记录曾观察到的情况，可能同时为真，也包含初始化状态。`event_count` / `event_bytes` 在单条大小校验后、发布前累计，**不是成功发布或归档量**，不含终态。摘要不含事件正文或 Runtime 引用；提交后终态发布的降级可能只体现在内存与摘要中。

## 5. 选择事件与读取协议

`ui_stream`、`types`、`exceptions`、`archive`、`sse` 可用标准库核心。默认 `codec`、`reader` 需要 `[json]`；实际 `RedisLiveStore` 还需要 `[redis]`。可选 profile 就是一组现成事件/适配器，核心不强制使用它们。

默认 `Event(event=None, data=...)` 表示业务消息，`ControlEvent.STREAM_END` 表示结束，`STREAM_RESET` 表示宿主要求重新选择执行。`stream_id` 保存 Redis 真正生成的消息 ID；宿主合成的控制提示通常留空，避免伪造续传位置。字段赋值被冻结，但 `data` 内部对象没有深度冻结，接受后不能继续修改。默认 JSON 编码拒绝 NaN/Infinity。

### Redis 尾流转 SSE

写端把 `RedisLiveStore` 包装为 `BoundRedisPublisher(store, execution_key)`，再作为 `StreamRuntime` 的 `publisher` 传入；codec 选择匹配的 `JsonEventCodec()`。读端使用同一个 store 和 execution key。宿主创建/关闭 Redis client，并把它注入 store；两个适配器的构造参数见 [redis.py](../src/ui_stream/redis.py)。

宿主先鉴权、选择 execution 和对应 key，再把已经创建的 store 传给下列函数。函数产出的帧应由 HTTP 层发送/刷新，它不创建 HTTP 响应或查快照：

```python
from ui_stream.reader import iter_live, normalize_cursor
from ui_stream.sse import SSEBody


def sse_frames(store, key, last_event_id, state_check=None):
    cursor = normalize_cursor(last_event_id)
    events = iter_live(
        redis_store=store, redis_key=key, cursor=cursor, state_check=state_check
    )
    return SSEBody(events)
```

`normalize_cursor` 把空/假值转为 `0-0`，否则要求两段 ASCII 十进制 uint64，合法前导零原样保留；不验证事件存在性或权限。`iter_live` 只产出 Event 或 None，None 由宿主转成 SSE 注释心跳；心跳不推进游标或刷新业务空闲时间。示例沿用 `block_ms=15000`（单次最多等待 15 秒）和 `idle_timeout=300`（业务空闲期限 5 分钟）；默认无状态提示时，持续无业务事件达到期限也会结束迭代。长任务可按需调整这两个参数，见 [reader.py](../src/ui_stream/reader.py)。

长连接可用 `state_check` 复核权限和执行是否仍有效：无参、默认每 15 秒首次及后续检查，返回控制 Event 或 None，权限异常直接传播。宿主返回结束提示时先非阻塞排空已发布尾流；读到真实结束事件则保留其游标，否则原始空读后才发合成结束。只有坏 payload 的批次不算尾流为空。reset 提示立即关闭旧流。

`RedisLiveStore` 每次追加在 Lua 内执行 XADD 与滑动 TTL 刷新，返回附有真实 ID 的新事件；没有自动重发/去重。读取时建议 `decode_responses=False`：坏 payload 可逐条跳过，批次扫描游标仍前进。若客户端预解码在返回任何 ID 前就失败，则变成 LiveUnavailable。读取遇到配置的实时异常时迭代结束，**不会合成完成事件**；快照重取和重连由宿主决定。`block_ms<=0` 在默认适配器中表示不阻塞，不是 Redis `BLOCK 0` 的无限等待。

### 同步 SSEBody 的资源边界

`SSEBody(events, *, encoder=...)` 是惰性同步 `Iterator[bytes]`，要求传入已取得的 Iterator。默认编码 Event/None；自定义编码器处理每个元素并返回 bytes，可映射宿主异常。body.close 在首次 next 前、正常耗尽、读取或编码错误时向源 close 透传且最多一次。直接 close 失败传播；已有读取/编码错误期间的普通清理失败只记录类型，不替换原错误。编码器或自然耗尽清理的 StopIteration 转为带 cause 的 RuntimeError，避免消费者误当 EOF；显式 close 保留原异常。

HTTP 层应在退出时关闭 body。它只拥有传入的 iterator，不关闭外层 Iterable、共享客户端或生产任务。未启动生成器的 finally 不保证执行，阻塞 next 不能被 close 中断；不支持并发调用和 async iterator。可运行自定义编码示例见[迁移说明](migration.md)。

### 宿主保存有界 JSON 事件列表

`encoded_archive_bytes(events, event_size=...)` 计算完整紧凑 JSON 数组字节，包含方括号和逗号；默认用 `model_dump(mode="json")` 计量单条记录。`fit_archive_events(existing=..., incoming=..., max_bytes=..., event_size=...)` 返回 **(新列表、接收的新事件数、是否丢弃了新事件)**，保留已有数据并只接收能放下的连续前缀，不修改输入列表或自动去重。

若持久化记录包含公开序列化排除的内部字段，`event_size` 应计入这些字段；只有它与实际紧凑 JSON 编码一致，结果才是实际存储大小。这里仅提供计量和裁剪，查询、事务、权限与归档回放仍在宿主实现。

需要重试去重时使用 `append_archive_events(existing, incoming, *, max_bytes, identity, event_size=None) -> ArchiveAppendResult`。它先按宿主提供的 Hashable ID 去重，再调用 fit；只有 None 表示无身份，不按正文去重或生成 ID。已有事件含重复/超大记录也原样保留；incoming 中同 ID 首条优先。回调异常传播。

结果 `events` 是新列表；`appended_count`、`duplicate_count`、容量 `dropped_count` 之和等于本批 incoming 长度，`truncated` 为 dropped_count > 0。先去重再裁剪，首条被拒绝后不接收后面更小候选。宿主保证身份不可变、输入接收后不被修改，并在自己的事务内保存结果。完整示例和边界见[迁移说明](migration.md)。

## 6. 从内嵌源码迁移

宿主固定所需版本和 extra，移除同名内嵌 `ui_stream` 以免遮蔽安装包，重新生成依赖锁并核对实际导入位置。业务模型、权限、任务异常、事务与读取接口留在宿主；仓库示例/Demo 不应作为 wheel 中可导入的业务依赖。
