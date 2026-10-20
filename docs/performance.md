# 性能基线

[English](performance.en.md) · [文档导航](README.md)

本机短突发里，事件到解析的最坏 p99 为 **2.897 ms**，短 worker 峰值 RSS 为 **56.375 MiB**，都落在本文选定的 **50 ms** / **128 MiB** 预算内。同样 512 个事件，SQLite 的生产区间约为临时内存的 **13.1 倍**。

样本观察的是传输、核心协调和 SQLite 的成本。生产容量、SLO 和 Demo 的持续运行要另测，界限见文末。

## 结论

预算按这个用法来定：单个有限任务，message 正文 128 字节（另含 JSON/SSE 元数据），每 **500 ms** 更新一次（2 Hz），最多 8 个本机观察者。500 ms 里留 10% 给事件发出到解析完成的额外延迟，即 **50 ms**。完整短 worker 的峰值 RSS 预算为 **128 MiB**。这是本文选定的工程预算。

| 项目 | 预算 | 实测 |
| --- | --- | --- |
| 事件到解析 | 50 ms | 最坏一次 p99 **2.897 ms**（预算的 **5.8%**）；所有观测最大 **2.956 ms** |
| 短 worker 内存 | 128 MiB RSS | runtime HTTP 峰值最大 **56.375 MiB** |
| 512 个事件的生产区间 | 本文不另设时间预算 | runtime 中位数：临时内存 **5.80 ms**，SQLite **75.68 ms**，约 **13.1×** |

延迟和内存两行来自 9 个 runtime HTTP/SQLite 样本：1、4、8 个读者各重复 3 次。它们共交付 **2,496** 个有序业务帧和 **39** 个终态。64 个事件 ×（1+4+8）个读者 × 3 次 = 2,496 帧；（1+4+8）× 3 次 = 39 个终态，每个读者一次。完成时结果均已提交。峰值 RSS 含解释器、可选模块、SQLite、服务端和本机客户端。

## 场景

`runtime` 是完整的 `StreamRuntime`。`raw` 与它使用相同的 codec、live/archive JSON 编码、游标、每事件一次帧接收、16 条一批的 checkpoint，并在 commit 之后发布终态。摘要、数量、批次和结果用来核对工作量。`raw` 省去锁、容量判断、完整性状态和恢复，不具备同等故障保障。

| 层次 | 负载 | 持久化 | 对照 |
| --- | --- | --- | --- |
| core | 512 个 progress 事件、128 字节 ASCII 文本字段（另含元数据）、单生产者、同步帧摘要接收器 | 临时内存 / SQLite 最新快照 | `StreamRuntime` / raw loop |
| SSE 编码 | 512 个相同 compact JSON、`id: <step>-0`、LF 帧 | 无网络、flush 或存储 | ui-stream JSON/SSE profile / 已安装的 sse-starlette `ServerSentEvent` |
| HTTP + SQLite | 64 个事件、单生产者、localhost 上 1/4/8 个读取连接 | 最新快照；尾部、终态与结果原子提交 | 同一宿主与适配器上的 runtime / raw 生产者 |

HTTP 复用 Demo 的 handler、MemoryChannel 和 SQLite repository，事件由基准生成。全部读者就绪后才开始生产，每帧 flush，并断言每人收到有序业务帧和一次终态。结果覆盖单进程标准库宿主、本机客户端和 SQLite。

图使用的基准参数是 backlog=8、无 pacing、checkpoint=16。Demo 是 backlog=5、checkpoint=3，按 500 ms 间隔更新，最多四个任务。图中的速度是无间隔突发，不是 Demo 那段持续运行。基准也不运行 `DemoService` 的四任务准入。

## 怎样读图

每组重复三次，每次新建子进程，交替正序和逆序，没有单独预热，也没有逐事件 sleep。第一次操作是冷启动，后面的操作自然变热；有限突发用来观察排队。

点是三次原始结果，中线是中位数，须线是最小到最大。连线只帮助顺着看。p50/p95/p99 在每次样本内按 nearest-rank 单独计算，三次不合并，也不把分位数再平均。须线是这三次的范围，不是置信区间。

## 图 1：读者增加时的延迟

![1、4、8 读者的最慢首帧与每次样本 delivery p95，毫秒](performance-figures/http-latency.png)

读者从 1 增到 8，延迟上升，raw 与 runtime 的范围大体接近。每个读者都要编码、发送和解析一帧。调度、编码、队列各占多少，没有 profiler 数据。

横轴是实测的 **1、4、8** 个读者，纵轴是 **ms，越低越好**。首帧从 HTTP 请求开始，到解析出第一个业务帧，包含连接和就绪屏障，不含心跳。每一档只有 1、4 或 8 个首帧观察，所以图上的首帧 p95 就是最慢的那个观察者。delivery p95 属于各次样本。

## 图 2：唯一事件与总投递吞吐

![同一完整 HTTP 波次的唯一事件吞吐与总帧吞吐](performance-figures/http-throughput.png)

runtime 中位数依次为 **4,266 → 3,096 → 2,575 events/s**，以及 **4,266 → 12,383 → 20,600 frames/s**。

左图是唯一业务事件/秒。事件数固定，速率下降表示从最早一次请求到最后一名读者结束的完整波次变长。右图是投递业务帧/秒：一个事件发给 8 人计 8 帧，人数增加时分子变大，所以右图上升。终态计入波次耗时，不计入分子。同一读者数下，数值越高，这波完成得越快。

## 图 3：SQLite 与临时内存的成本

![相同 512 事件的 volatile 与 SQLite 生产区间耗时，毫秒](performance-figures/core-persistence.png)

同样 512 个事件，runtime 中位数从临时内存的 **5.80 ms** 变为 SQLite 的 **75.68 ms**，约 **13.1×**。raw 与 runtime 的 SQLite 范围重叠。

纵轴是生产区间的 **ms，越低越好**。每 16 条打开一次连接并提交 checkpoint，约 6.25% 的 send 因此带上连接、编码和事务。单次 p50 仍在微秒级，p95 包含这次毫秒级提交。13.1× 是整条存储路径的耗时；磁盘、缓存、SQLite、编码和锁没有拆开。

每个样本使用新建的临时库，只保留最新快照。每次 checkpoint 是独立连接上的短事务，最终结果原子提交。日志模式为 WAL，历史结果记录的是 `synchronous=1`（NORMAL）。这和 FULL 的断电持久性不同，断电可能丢失已提交事务，见 [SQLite 同步语义](https://www.sqlite.org/pragma.html#pragma_synchronous)。建表、创建 execution 和最终校验不在这 512 个事件的生产区间里。临时内存的值随进程退出消失。

## 适用范围

这组样本可以核对三件事：

- 所选本机短突发有没有超出 50 ms 和 128 MiB。
- 这条 SQLite 路径相对临时内存的整体成本。
- 读者从 1 增到 8 时，延迟、唯一事件吞吐和总投递吞吐各自怎么变。

下列情况要单独验收：

- Demo 持续以 2 Hz 服务 8 个观察者时的延迟、长期内存增长、浏览器、远端网络、计算和恢复耗时。
- finish 时仍有业务事件尚未 checkpoint。512 和 64 都能被 checkpoint=16 整除，样本里没有这条尾巴。
- TLS、代理、Redis 服务、远端数据库、多进程广播、慢客户端，以及清空 SQLite 或内核缓存之后的耗时。
- 置信区间、尾延迟 SLO、饱和容量和生产机器规划。测量是一台机器上的三次短 ASCII 突发、单写入者。调度、电源、温度和其他应用都会影响结果。
- ASGI 网络，以及框架之间的速度排名。编码对照只使用已安装的 sse-starlette 3.2.0，比较的是等价字节。职责和恢复边界见 [方案选型](comparison.md)。

## 指标口径

- **首事件**：core 和编码层是首次同步操作完成的时间。HTTP 是从请求开始到解析出首个业务帧，包含连接和就绪屏障，不含心跳。
- **延迟**：`send_ns` 包含同步 checkpoint。HTTP `delivery_ns` 从 `send()` 前构造事件，到客户端解析完一整帧，使用同一单调时钟，包含排队、编码、socket 和解析。
- **分位数**：各样本独立计算 nearest-rank p50/p95/p99，三次重复不合并、不平均分位数。core 的首事件每次只有一个观测，三个分位数相同。
- **吞吐**：core 和编码层以生产区间为分母。HTTP 以完整波次为分母，同时报告唯一事件和投递帧（事件数 × 读者）。终态计入耗时，不计入分子。
- **内存与磁盘**：新 worker 的峰值 RSS 含解释器、可选模块、SQLite、服务端和本机客户端，不是 core 的增量。`database_bytes` 是结束时数据库和 WAL 的大小，不是总写入量或峰值。

## 复现

全部 **36** 个样本保存在[原始 JSON](../benchmarks/results/baseline-2026-10-18.json)：2026-10-18，Python 3.11.10、SQLite 3.54.0、Apple M3 Pro（12 逻辑 CPU、36 GiB）、arm64、macOS 27.0.1。可选编码依赖为 Pydantic 2.10.4、sse-starlette 3.2.0。

这是不可变[源码 `7248096`](https://github.com/0RAJA/ui-stream/tree/72480966c9bb154362b2a378ec24eab633205f7b) 对应的历史 macOS 测量，保留原始字节与源码摘要。后续代码与 Linux 需要分开重测。[派生序列](performance-figures/series.json)保留原值、统计口径和原文件 SHA256。每张 PNG 都有同名 SVG。

在仓库根目录、已安装核心包的 Python 环境中运行。脚本不安装依赖。benchmark、Demo 和示例不进入 wheel/sdist。新测量写入独立文件，不覆盖这份历史基线。

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

可选绘图需要本机已经装有 ReportLab。macOS 用系统 `sips` 导出 PNG；没有 `sips` 时仍会生成 SVG。`--pdf-output` 可以把 PDF 写到仓库外。核心、Demo 和基准运行本身不需要 ReportLab。

```sh
python -m benchmarks.plot --input benchmarks/results/baseline-2026-10-18.json --output docs/performance-figures
```

硬上限：core 和编码 1,000 条、HTTP 256 条、8 个读者、正文 4,096 字节、3 档连接数、3 次重复。worker 最长 30 秒墙钟、15 秒 CPU、128 个文件描述符。Linux 地址空间限 512 MiB，整组 120 秒后停止调度。macOS 由负载、读者和 channel 容量约束内存，没有另设地址空间限制。smoke 为 16 个 core/HTTP 事件、1 个连接、2 次重复。测试不设机器速度阈值。

当前 Linux launcher 在 Python 启动前为子环境设置 `MALLOC_ARENA_MAX=2`，限制 glibc arena 预留，同时保留 512 MiB 上限和 1/4/8 读者覆盖，见 [GNU libc 说明](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html)。新结果的 `worker_limits` 记录实际限制和 allocator 环境。这份历史 macOS JSON 早于这些字段。

样本只使用合成数据和 loopback，临时库会删除。JSON 记录环境版本和相对源码的 AST 摘要，不含主机名、用户名、解释器绝对路径或本机路径。
