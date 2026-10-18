# 性能：首版可复现基线

[English](performance.en.md)

这组基准回答小规模合成事件流的时间主要花在哪里，不用于宣称生产容量上限。core 负责协调宿主提供的事件、发布和归档接口；HTTP 宿主与持久化实现是独立选择。

## 复现

在仓库根目录使用已有 Python 环境，先安装核心包。`benchmarks/`、`demos/` 与 `examples/` 都留在仓库，不进入 wheel 或 sdist。脚本不会自动安装依赖。

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

请替换为自己环境的 Python 路径。公开原始结果是 [baseline-2026-10-18.json](../benchmarks/results/baseline-2026-10-18.json)，其中环境信息和每次样本的原始时间为准。换机器应重新执行。仅当环境已经安装 sse-starlette 时才运行可选编码比较；默认 core/HTTP 基准只需要标准库和已安装的核心包。

## 负载与边界

| 层次 | 负载 | 持久化 | 对照 |
| --- | --- | --- | --- |
| core 微基准 | 512 个 progress 事件、128 字节 ASCII 正文、单生产者、同步帧摘要接收器 | 分开运行内存临时状态和 SQLite 最新快照 | `StreamRuntime` 与 raw loop |
| SSE 编码 | 512 个相同 compact JSON、`id: <step>-0`、LF 换行帧 | 无持久化、网络或 flush | ui-stream JSON/SSE profile 与已安装的 sse-starlette `ServerSentEvent` |
| HTTP + SQLite | 64 个事件、单生产者、localhost 同时 1 → 4 → 8 个独立读取连接 | SQLite 最新快照；尾部、终态与业务结果原子提交 | 同一 HTTP/SQLite 适配器，分别使用 runtime 与 raw 生产者 |

raw 和 runtime 使用相同 codec、live/archive JSON 编码、游标分配、每事件一次帧接收、16 条一批 checkpoint，以及先 commit 再发布终态的顺序。两条路径都会对每个事件编码 live 和 archive 视图。raw 有意省去 runtime 的锁、容量判断、完整性状态与故障恢复，只用于隔离正常路径成本，不具有同等故障保障。校验帧摘要、数量、批次与最终结果，避免工作量不等价。

SQLite 使用 Demo repository 的 WAL、连接默认 synchronous 设置（结果记录实际值）、每次 checkpoint 独立短事务与连接，以及最终结果原子事务。每个样本新建临时数据库，只保留最新快照，不存完整事件历史。本机实际 `synchronous=1`（NORMAL）；测的是事务提交，不能当作 FULL 模式的断电持久性，WAL/NORMAL 可能在断电后丢失已提交事务，见 [SQLite 官方同步语义](https://www.sqlite.org/pragma.html#pragma_synchronous)。core 的建表、创建执行与最终校验不计入生产者耗时；无持久化分支只保留进程内最新值和结果，进程退出即丢失。

HTTP fixture 复用 `demos.sqlite` 的真实 HTTP handler、MemoryChannel 和 SQLite repository，但由 benchmark 自己生成合成事件，不使用有固定步数和间隔的 `DemoService`，也不测其四任务准入限制。等全部读取者到达后才生产，每帧 flush，所有读取者都必须按序收到每个业务事件及一次终态。HTTP 结果覆盖标准库单进程宿主、本机客户端和 SQLite，不能推出 ASGI 部署或 core 的最大连接数。

实验 HTTP server 显式配置 listen backlog=8，未修改的 Demo 默认是 5；raw/runtime 共用此设置。最初未出结果的一次尝试暴露默认 backlog 下的连接重置与准入失败清理死等，fixture 已改为失败时先释放读取者再 join。失败尝试不混入成功样本。

## 指标含义

- **首事件**：core/编码层测首次同步操作完成时间；HTTP 从请求开始到解析出首个业务帧，包含连接建立及读取者就绪屏障。心跳不计为首事件。
- **延迟**：`send_ns` 包含同步 checkpoint；HTTP `delivery_ns` 从调用 `send()` 前构造事件开始，到客户端解析完整帧结束，使用同一单调时钟，包含排队、编码、socket 和解析。
- **分位数**：每次样本独立使用 nearest-rank p50/p95/p99，保留三次重复。core 首事件每次只有一个观测，因此三个分位相同，不能当成 512 个首事件样本。不要将多次分位数平均后声称是合并分位数。
- **吞吐**：core/编码层以生产者区间为分母；HTTP 从最早请求到最晚读取完成。同时报告唯一生产事件/秒与投递事件/秒（事件数 × 读取者）。终态不计入业务事件分子，但计入耗时。
- **内存**：每个新 worker 的进程峰值 RSS，包含解释器、可选依赖、SQLite、HTTP 服务与本机客户端，不是 core 增量，也没有拆分服务端和客户端。`database_bytes` 是结束时数据库/WAL 文件大小，不是总写入量或磁盘峰值。
- **持久化开销**：对比同一 core 实现有/无 SQLite 的耗时区间，包含连接、事务、快照编码及文件系统缓存因素，不能把整个差值归因给 core。

## 资源上限与采样

默认每组重复三次，每个样本新建子进程，重复之间交替正序/逆序，不单独预热；首操作是冷启动状态，后续事件自然热身。无逐事件 sleep 或生产者节奏控制，有限突发用于暴露排队。

输入硬上限：core/编码 1,000 条、HTTP 256 条、8 个读取连接、正文 4,096 字节、3 档连接数、3 次重复。每个 worker 最长 30 秒墙钟、15 秒 CPU、128 个文件描述符；Linux 另限地址空间 512 MiB。suite 120 秒后停止调度。macOS 没有施加不可靠的可移植地址空间限制，由负载、读取者与 channel 容量在结构上约束内存。smoke 为 16 个 core/HTTP 事件、1 个连接、2 次重复。测试验证协议、结果和上限，不设机器速度阈值。

只使用合成数据与 loopback，临时数据库自动删除。公开 JSON 记录硬件、OS、Python、依赖版本和相对源码 AST 摘要（忽略格式与注释），不包含主机名、用户名、解释器绝对位置或本机路径。

## 相关方案：功能接入比较与实测分开

官方源核查日期：2026-10-18。明确区分查阅版本和本机实测版本，不提供不等价框架排行榜。

| 方案 | 官方源与查阅版本 | 接入、持久化边界 | 本次实测 |
| --- | --- | --- | --- |
| ui-stream | 当前 checkout，JSON 记录源码摘要 | 与框架无关的 runtime ports；宿主拥有事务、fencing、结果和传输，最新快照恢复由适配器实现 | core/raw、参考 HTTP/SQLite；安装 Pydantic 时测 JSON/SSE 编码 |
| sse-starlette | [v3.5.0 release](https://github.com/sysid/sse-starlette/releases/tag/v3.5.0)、[官方用法](https://github.com/sysid/sse-starlette) | ASGI `EventSourceResponse`，处理 ping、断连与发送超时；事件来源和持久化由应用提供，可作为 ui-stream 宿主传输层 | 本机 **3.2.0**，只比较同 JSON/LF/ID 字节的编码，不比较 ASGI/uvicorn 吞吐 |
| Django EventStream | [官方 metadata 5.3.4](https://raw.githubusercontent.com/fanout/django-eventstream/master/pyproject.toml)、[存储说明](https://github.com/fanout/django-eventstream#event-storage) | Django channel/view 和权限；可选数据库事件先存后发，支持历史恢复；语义不同于宿主最新快照与结果原子事务 | 未安装，不安装新依赖，不提供实测数值 |
| Starlette `StreamingResponse` | [官方响应文档](https://github.com/Kludex/starlette/blob/main/docs/responses.md)、[1.8.0 release](https://github.com/Kludex/starlette/releases/tag/1.8.0) | ASGI 流响应接受同步/异步迭代器；SSE framing 与持久化由应用选择，官方文档将 SSE 指向 sse-starlette | 本机 0.52.1，不独立测试框架；编码 adapter 不代表 `StreamingResponse` 性能 |

做应用级对比前，需要对齐 HTTP server、事件校验、编码、生产节奏、逐事件 flush、队列边界、重连行为、归档保留和 commit-before-terminal 语义；否则吞吐差异混合了不同组件职责。

## 局限

只测一台本机、短 ASCII 合成突发、单写入者；没有 TLS、反向代理、浏览器、Redis 服务、远端数据库、多进程广播或慢客户端故障注入，不清空 SQLite/内核缓存。调度、电源/温度状态及其他本机应用会影响结果。三次短测用于复现和诊断，不能支持置信区间、尾延迟 SLO、饱和容量或生产资源规划。其他负载需要显式修改 harness 并单独审阅。

## 已记录结果

记录于 2026-10-18：Python 3.11.10、SQLite 3.54.0、Apple M3 Pro（12 个逻辑 CPU、36 GiB 内存）、arm64、macOS 27.0.1。完整保留 **36** 个样本。可选编码依赖：Pydantic 2.10.4、sse-starlette 3.2.0。斜线依次表示第 1 / 2 / 3 次重复；分位数属于各次样本，没有合并。

| Layer / 层次 | Implementation / 实现 | Store / 存储 | Events/s · r1 / r2 / r3 | send p95 µs · r1 / r2 / r3 |
| --- | --- | --- | --- | --- |
| core | raw | none | 90,585 / 96,608 / 95,771 | 11.12 / 11.79 / 12.21 |
| core | runtime | none | 83,507 / 90,294 / 88,318 | 14.00 / 12.17 / 13.54 |
| core | raw | sqlite | 7,048 / 6,661 / 6,388 | 1,740.50 / 1,914.25 / 1,793.62 |
| core | runtime | sqlite | 7,032 / 6,765 / 6,496 | 1,642.92 / 1,726.17 / 1,946.67 |
| encoding | native | none | 298,804 / 320,760 / 271,570 | 4.12 / 3.08 / 4.29 |
| encoding | sse-starlette | none | 279,102 / 280,465 / 281,693 | 4.50 / 4.04 / 4.42 |

| HTTP / SQLite | Readers / 读取者 | Delivered events/s · r1 / r2 / r3 | First event p95 ms · r1 / r2 / r3 | Delivery p95 ms · r1 / r2 / r3 |
| --- | --- | --- | --- | --- |
| raw | 1 | 4,404 / 3,911 / 4,209 | 3.023 / 2.784 / 2.711 | 0.647 / 0.452 / 0.429 |
| runtime | 1 | 4,266 / 4,288 / 4,044 | 2.737 / 2.867 / 2.993 | 0.466 / 0.419 / 0.660 |
| raw | 4 | 13,755 / 12,635 / 13,520 | 4.123 / 4.452 / 3.979 | 1.500 / 1.638 / 1.551 |
| runtime | 4 | 12,035 / 12,728 / 12,383 | 5.472 / 4.946 / 5.192 | 1.709 / 1.565 / 1.599 |
| raw | 8 | 20,684 / 21,716 / 20,586 | 6.739 / 5.760 / 6.456 | 2.623 / 2.666 / 2.793 |
| runtime | 8 | 22,447 / 20,600 / 19,953 | 5.479 / 6.202 / 6.866 | 2.643 / 2.834 / 2.673 |

| Runtime latency / 延迟 | Unit / 单位 | r1 p50 / p95 / p99 | r2 p50 / p95 / p99 | r3 p50 / p95 / p99 |
| --- | --- | --- | --- | --- |
| core / none | µs | 10.042 / 14.000 / 17.833 | 9.708 / 12.166 / 18.542 | 9.750 / 13.541 / 17.292 |
| core / sqlite | µs | 11.125 / 1642.916 / 2323.791 | 11.000 / 1726.166 / 2584.917 | 11.167 / 1946.667 / 2533.125 |
| http / sqlite / 1 readers | ms | 0.290 / 0.466 / 0.589 | 0.278 / 0.419 / 0.560 | 0.282 / 0.660 / 0.679 |
| http / sqlite / 4 readers | ms | 1.055 / 1.709 / 1.925 | 0.960 / 1.565 / 1.684 | 0.946 / 1.599 / 1.701 |
| http / sqlite / 8 readers | ms | 1.692 / 2.643 / 2.870 | 1.860 / 2.834 / 2.897 | 1.682 / 2.673 / 2.825 |

无持久化时 core runtime 为 83,507–90,294 条/秒，同 I/O raw loop 为 90,585–96,608。加入 SQLite 后 runtime 为 6,496–7,032 条/秒，与 raw 的 6,388–7,048 重叠；此负载主要由事务工作主导，不能据三次短测建立统计显著的排名。两种 SSE 编码实现的范围也重叠。

所有 case 的整体 worker 峰值 RSS 为 54.86–56.56 MiB；8 读者 HTTP runtime 为 56.19–56.38 MiB。每个持久化样本结束时 SQLite 文件集合为 49,152 字节。runtime 的 SQLite 生产区间为 72.8 / 75.7 / 78.8 ms，内存临时状态为 6.1 / 5.7 / 5.8 ms；这是所选归档适配器、连接生命周期和本机文件系统的整体成本。

8 个 HTTP 读者时，runtime 合计投递 19,953–22,447 个业务帧/秒，唯一生产事件吞吐为 2,494–2,806 条/秒。这只是已注明配置的参考宿主有限突发观测，不能作为连接容量上限或 ASGI 框架比较。
