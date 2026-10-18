# 性能：首版可复现基线

[English](performance.en.md)

这组基准回答小规模合成事件流的时间主要花在哪里，不用于宣称生产容量上限。core 负责协调宿主提供的事件、发布和归档接口；HTTP 宿主与持久化实现是独立选择。

## 先看结论：本机传输子项可接受，整体为条件通过

这里给出一个**工程验收示例**，由维护工作选择，并非通用行业阈值：单个有限任务、128 字节 message（另含 JSON/SSE 元数据）、每 500 ms 更新一次（2 Hz）、最多 8 个本机观察者。给额外的事件到解析延迟分配更新周期的 10%，即 **50 ms**；给完整短 worker 分配 **128 MiB** RSS。浏览器绘制、远端网络、计算耗时和恢复时延另行验收。

历史 9 个 runtime HTTP/SQLite 样本在每次短突发中都满足这个传输预算：最坏一次 p99 为 **2.897 ms**，占预算 **5.8%**；所有已观测延迟的最大值为 **2.956 ms**。这足以支持继续本地功能验收。原始数据测的是突发，不是持续 2 Hz 的实际 Demo；因此**整体为条件通过，尚未证明生产 SLO**。

| 验收子项 | 明确预算或正确性要求 | 实测证据 / 结论 |
| --- | --- | --- |
| 参考宿主短突发完整性 | 每个读者收到有序业务帧及一次终态，完成后结果已提交 | 9 个 runtime 样本共 2,496 业务帧、39 终态；全部断言通过 |
| runtime 参考宿主额外传输延迟 | 每次样本 p99 ≤50 ms | 最坏 2.897 ms；此子项通过，不是持续尾延迟承诺 |
| 短 worker 内存 | 峰值 RSS ≤128 MiB，含服务端、本机客户端及可选模块 | runtime HTTP 最大 56.375 MiB；短测通过，长期增长未验证 |
| 实际 Demo 的 2 Hz / 8 人准入 | 保持 500 ms 更新节奏、8 人同时接入 | 未验证：实际 Demo backlog=5、checkpoint=3；基准 backlog=8、无 pacing、checkpoint=16 |
| 断开 / 重启后恢复耗时 | 需由产品先定义恢复时间预算 | 本基线未计时；单测验证恢复/回滚正确性，不能代替时延验收 |
| 非空尾部完成、断电持久性 | 需覆盖对应完成事务与持久性模式 | 基线 512 和 64 均为 checkpoint=16 的整数倍，finish 无业务 pending；SQLite WAL/NORMAL 不能等同 FULL 断电持久性 |

## 图 1：读者增加时，延迟怎样变化

![1、4、8 读者的最慢首帧与每次样本 delivery p95，毫秒](performance-figures/http-latency.png)

横轴只含真实测过的 **1、4、8** 个读者，纵轴是 **ms，越低越好**。每组保留三次原始点，中间线为三次结果的中位数，须线为 min–max；连线只引导读图，不是额外测量或拟合。首帧每次只有 1/4/8 个观察值，这里的 p95 等于最慢观察者，不能解释为稳健的尾 SLO。delivery p95 是各次样本自己的分位数，不是三次合并后的 p95。

**实测：**读者增加时此参考路径延迟上升，runtime 与同宿主 raw 的范围大体接近。**解释：**代码为每个读者编码、发送并解析帧，更多线程与 socket 会增加工作；具体有多少来自调度、编码或队列尚无 profiler 证据，不能把全部增长归因于 runtime。

## 图 2：总投递增加，不等于生产者变快

![同一完整 HTTP 波次的唯一事件吞吐与总帧吞吐](performance-figures/http-throughput.png)

两面板共用真实读者数。左侧是 **唯一业务事件/秒**，右侧是 **投递业务帧/秒**；分母都是最早请求至最后读者结束的完整波次时间。一个事件投给 8 人计 8 帧，终态不计入分子。数值越高表示此固定负载完成得越快，不是支持的最大连接数。

runtime 三个点的唯一事件吞吐中位数约 **4,266 → 3,096 → 2,575 events/s**，总投递约 **4,266 → 12,383 → 20,600 frames/s**。右图上升主要包含 fanout 的计数乘数；不能据此声称核心在高并发下变快，或据 raw/runtime 三次重叠范围排出胜负。

## 图 3：SQLite 成本应与核心协调成本分开

![相同 512 事件的 volatile 与 SQLite 生产区间耗时，毫秒](performance-figures/core-persistence.png)

纵轴为相同 **512 个事件的生产区间 ms，越低越好**；保留三次原始点与中位数/min–max。runtime 的中位数从临时内存的 **5.80 ms** 变为 SQLite 的 **75.68 ms**（两组中位数之比约 **13.1×**），raw 的 SQLite 范围与 runtime 重叠。

**已知结构：**每 16 条打开连接并提交一次 checkpoint，带来连接、编码与事务工作；约 6.25% 的 send 包含 checkpoint，所以 p50 仍在微秒级而 p95 会包含毫秒级工作。**尚不能分解：**磁盘、文件缓存、SQLite、编码和锁各占多少。不能将 13.1× 都称为 runtime 开销，也不能据短测优选某个框架。

## 图表复现与来源

图表来自未改动的 [36 样本原始 JSON](../benchmarks/results/baseline-2026-10-18.json)，对应不可变源码 `7248096`，不是本轮修复后的重新测量。[派生序列](performance-figures/series.json)保留原值、统计口径和原始文件 SHA256。每张 PNG 同目录有同名 SVG；均无需外部图片服务。

下面的可选维护工具使用已有 ReportLab；macOS 用系统 `sips` 生成 PNG。没有 `sips` 时仍导出 SVG；普通核心、Demo 与 benchmark 不需要 ReportLab。PDF 可通过 `--pdf-output` 导向仓库外目录。不要在用户验收 Demo 或本机忙碌时用新测量覆盖历史基线。

```sh
python -m benchmarks.plot --input benchmarks/results/baseline-2026-10-18.json --output docs/performance-figures
```

## 复现

在仓库根目录使用已有 Python 环境，先安装核心包。`benchmarks/`、`demos/` 与 `examples/` 都留在仓库，不进入 wheel 或 sdist。脚本不会自动安装依赖。

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

请替换为自己环境的 Python 路径。公开原始结果是 [baseline-2026-10-18.json](../benchmarks/results/baseline-2026-10-18.json)，其中环境信息和每次样本的原始时间为准。换机器应重新执行。仅当环境已经安装 sse-starlette 时才运行可选编码比较；默认 core/HTTP 基准只需要标准库和已安装的核心包。

canonical JSON 是不可变[源码提交 `7248096`](https://github.com/0RAJA/ui-stream/tree/72480966c9bb154362b2a378ec24eab633205f7b) 对应的历史 macOS 测量。保留原始字节及当时源码摘要；之后的 Linux worker allocator 策略会改变当前 harness，重新运行会记录当前源码指纹，不覆盖或重标这份历史基线。不能由 macOS 结果声称已有 Linux 性能实测。

## 负载与边界

| 层次 | 负载 | 持久化 | 对照 |
| --- | --- | --- | --- |
| core 微基准 | 512 个 progress 事件、128 字节 ASCII 文本字段（另含事件元数据）、单生产者、同步帧摘要接收器 | 分开运行内存临时状态和 SQLite 最新快照 | `StreamRuntime` 与 raw loop |
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

Linux 下，suite 和协议测试共用 launcher，在 **Python 子解释器启动前** 将复制的子进程环境设为 `MALLOC_ARENA_MAX=2`，约束 glibc allocator arena 预留，避免多个 arena 仅因地址空间预留就挤占 512 MiB 预算。保留原 512 MiB 上限和 1/4/8 读者覆盖，不修改父进程环境，见 [GNU libc arena 配置说明](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html)。新 worker 结果通过 `worker_limits` 记录实际 CPU/FD/地址空间限制与 allocator 环境；历史 macOS JSON 生成于此字段加入之前。

只使用合成数据与 loopback，临时数据库自动删除。公开 JSON 记录硬件、OS、Python、依赖版本和相对源码 AST 摘要（忽略格式与注释），不包含主机名、用户名、解释器绝对位置或本机路径。

## 选型与实测分开

[方案选型](comparison.md)比较 StreamingResponse、sse-starlette、当前 FastAPI 内置 SSE、Django EventStream 与 LangGraph 的真实职责与恢复边界。只有本机已安装的 sse-starlette 3.2.0 参与等价字节编码测量；不是 ASGI 网络压测，没有框架容量排名。

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
