# ui-stream

[English](README.en.md) · [文档](docs/README.md) · [CI](https://github.com/0RAJA/ui-stream/actions/workflows/ci.yml)

**把后台任务的进度发给 UI，定期保存快照，让最终结果由业务自己的事务提交。**
核心同步、无第三方依赖；通过三个端口接入业务事件、实时通道和持久存储。

## 快速上手

Python ≥3.11，在虚拟环境中从源码运行：

```sh
python -m pip install --no-deps .
python -m examples.counter
python -m demos.sqlite --database ./demo.sqlite3 --port 8765
```

打开 `http://127.0.0.1:8765`。Demo 仅用标准库 HTTP/SQLite 和合成数据；macOS/Linux 可运行。
Counter 和 Demo 位于仓库的 `examples/`、`demos/`，需在仓库根目录运行；默认 wheel 只含核心。
发布到 PyPI 后可安装 `ui-stream==0.1.0a1`；import 名为 `ui_stream`。

## 适用边界

| 适合 | 由宿主负责 / 不保证 |
| --- | --- |
| 任务进度、最新快照、尽力实时尾流 | 权限、跨进程 fencing、任务重试与业务结果事务 |
| 有界缓冲、checkpoint、提交后再发终态 | 完整历史重放、断线无损、exactly-once |
| 同步 Python 宿主，自定义事件与存储 | 自动续跑、读者断开即取消、生产级 Demo 服务 |

## 扩展入口

实现 `EventCodec`、`LivePublisher`、`ArchiveStore` 即可接入，无须继承或修改 `StreamRuntime`。
内存 Counter 展示自定义事件与结果；SQLite Demo 展示持久快照、SSE 与故障恢复。

```python
from ui_stream import StreamPolicy
from examples.counter import make_counter

runtime, channel, store = make_counter(policy=StreamPolicy(checkpoint_event_count=1))
runtime.send(4)
runtime.send(9)
runtime.finish(status="DONE", completion=13)
assert store.snapshot.latest == 9 and store.snapshot.result == 13
```

| 安装范围 | 依赖 |
| --- | --- |
| 核心 wheel | 标准库 |
| 仓库 Counter / SQLite Demo | 标准库 + 已安装核心 |
| `[json]` | Pydantic ≥2.10,<3 |
| `[redis]` | Pydantic ≥2.10,<3，redis-py ≥3.5.3 |

![组件边界：任务调用 Runtime，宿主实现三个端口，UI读取实时尾流和快照](docs/diagrams/architecture.png)

[架构与完成顺序](docs/architecture.md) · [API 契约](docs/api.md) · [Demo](demos/sqlite/README.md) ·
[性能图表](docs/performance.md) · [方案选型](docs/comparison.md) · [贡献](CONTRIBUTING.md) · [安全](SECURITY.md) · [发布](docs/releasing.md)

CI 的源码矩阵覆盖 Python 3.11–3.14 / Linux、macOS；wheel、sdist 与 README 验证在 Linux 执行。
维护者：`raja <rajawwang@foxmail.com>`（GitHub [0RAJA](https://github.com/0RAJA)）。
[MIT 及来源说明](docs/license.md)；原版权声明保持不变。
