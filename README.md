# ui-stream

[English](README.en.md) · [文档](docs/README.md) · [CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml)

**把后台任务的进度发给 UI，分批保存进度快照，并在发送结束通知前提交最终结果。**

ui-stream 适合已经有后台任务和业务存储的 Python 应用。它负责实时发布、归档缓冲和完成顺序；使用它的应用（下文称“宿主”）提供任务调度、权限与真正的存储事务。核心同步、无第三方依赖。

## 快速上手

需要 Python ≥3.11 和已启用的虚拟环境。仅安装库时固定 alpha 版本，import 名为 `ui_stream`：

```sh
python -m pip install ui-stream==0.1.0a2
```

要运行示例，请先取得本仓库，并在**仓库根目录**执行以下命令。Counter 和 Demo 不包含在 wheel/sdist 中，不能仅安装 PyPI 包后直接运行：

```sh
python -m pip install --no-deps .
python -m examples.counter
python -m demos.sqlite --database /tmp/ui-stream-demo.sqlite3 --port 8765
```

Counter 打印实时文本帧和最终内存快照；SQLite Demo 在 `http://127.0.0.1:8765` 展示进度、故障与恢复，Ctrl-C 停止。Demo 使用合成数据和专用 SQLite，需 macOS/Linux。

下面用 Counter 看一次完整调用。4、9 是先后产生的进度值，13 是业务自己计算的最终结果；最新进度和最终结果是两份不同的数据：

```python
from ui_stream import StreamPolicy
from examples.counter import make_counter

runtime, channel, store = make_counter(policy=StreamPolicy(checkpoint_event_count=1))
runtime.send(4)
runtime.send(9)
runtime.finish(status="DONE", completion=13)
assert store.snapshot.latest == 9 and store.snapshot.result == 13
```

Counter 用内存演示接口，进程退出即丢失数据。它自定义了 `DONE` 终态；默认 JSON codec 使用 `SUCCESS` / `FAILED`，业务可以自行选择事件和状态。

## 接入自己的应用

创建一次执行后，组装 `StreamRuntime` 和三个实现对象：`EventCodec` 把业务值变成事件，`LivePublisher` 发送实时事件，`ArchiveStore` 保存进度和最终结果。无需继承 Runtime；完整实现可从 [Counter](examples/counter.py) 开始，再看 [架构与接入流程](docs/architecture.md) 和 [API](docs/api.md)。

![组件边界：任务调用 Runtime，宿主实现三个端口，UI读取实时尾流和快照](docs/diagrams/architecture.png)

| 能做什么 | 应用还需负责什么 |
| --- | --- |
| 有界缓冲、分批归档（checkpoint）、提交结果后再通知结束 | 任务调度、用户权限、事务、执行身份校验与重试 |
| 实时进度配合最新快照恢复 UI | HTTP/SSE 响应、快照查询、重连时的读取与衔接 |
| 自定义事件、实时通道与快照存储 | 按业务选择历史保留策略；核心不保证完整历史、断线无损或恰好一次投递 |

| 安装范围 | 用途与依赖 |
| --- | --- |
| 核心 wheel | 标准库；Runtime、端口和归档辅助函数 |
| 仓库 Counter / SQLite Demo | 标准库 + 已安装核心 |
| `[json]` | 默认 JSON 事件、Redis 游标读取协议和 SSE 帧；Pydantic ≥2.10,<3 |
| `[redis]` | 另含 Redis 实时存储适配器；Pydantic ≥2.10,<3，redis-py ≥3.5.3 |

[Demo](demos/sqlite/README.md) · [性能图表](docs/performance.md) · [方案选型](docs/comparison.md) · [贡献](CONTRIBUTING.md) · [安全](SECURITY.md) · [发布](docs/releasing.md)

CI 的源码矩阵覆盖 Python 3.11–3.14 / Linux、macOS；wheel、sdist 与 README 验证在 Linux 执行。
维护者：`raja <rajawwang@foxmail.com>`（GitHub [0RAJA](https://github.com/0RAJA)）。
[MIT 及来源说明](docs/license.md)；原版权声明保持不变。
