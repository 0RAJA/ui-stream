# SQLite 浏览器 Demo

[English](README.en.md) · [项目首页](https://github.com/0RAJA/ui-stream)

这个示例把一项 12 步任务接到浏览器：实时展示进度，用 SQLite 保存最新快照和最终结果，再演示刷新、实时故障和提交重试。它使用标准库 HTTP/SQLite 与无框架页面，仅有合成数据；需要 macOS/Linux 的 `fcntl`，无需 Django、Redis、Node/npm 或外部服务。

## 运行和观察

安装核心包后，在仓库根目录运行。Demo 不随 wheel/sdist 分发；数据库使用仓库外的专用文件，不要指向业务数据库：

```sh
python -m demos.sqlite --database /tmp/ui-stream-demo.sqlite3 --port 8765 --interval 0.5
```

打开 `http://127.0.0.1:8765`，选择场景并创建任务。默认每 0.5 秒一步，每 3 步保存一次快照；完成时最新快照和结果一起提交，页面显示 DONE。Ctrl-C 停止服务；同库第二进程会被拒绝，只绑定 loopback，无认证。

Demo 自定义了 `progress` / `finished` 事件、整数游标及 `DONE` / `ERROR` / `INTERRUPTED` 状态。它展示如何实现核心端口，不使用可选 JSON/Pydantic、Redis reader 或其游标协议。

| 场景 | 预期看到什么、由谁恢复 |
| --- | --- |
| 正常 | 共 12 步；宿主存储最终快照和结果，再发结束通知。 |
| 业务异常 | 第 6 步结束，保存错误结果，状态 ERROR；这是业务失败。 |
| 实时失败 | 第 4–7 步实时通道不可用；计算和 SQLite 继续，完整性 DEGRADED，业务仍可 DONE。 |
| checkpoint 失败 | 首次归档失败时 Runtime 保留 pending；后续事件触发恢复，保留 DEGRADED 提示。 |
| commit 失败 | 首次提交失败时 Runtime 未关闭、不发 finished；Demo 宿主约 1 秒后重试同一 Runtime 的最终提交，不重算任务。 |

## 刷新与恢复为什么这样做

刷新先读取 SQLite 的最新快照/结果，再跟随有界内存尾流；进度中间帧可能遗漏，但可以恢复当前 UI。读者断开不取消任务，已完成任务直接显示保存结果。重启服务会把遗留 RUNNING 标为 INTERRUPTED，保留已提交快照，不自动续跑计算。

按钮状态和服务端 `request_token` 防止一次创建请求重复生成任务；刷新会先确认未完成的创建 token。这是 Demo 宿主的创建幂等，不表示核心保证事件恰好一次投递。若生产者已关闭但还未确认持久终态，客户端停止自动 SSE 重订阅，继续每秒查询快照并允许手动恢复。

## HTTP 接口与实现入口

| HTTP | 用途 |
| --- | --- |
| `POST /api/jobs` | 提交 JSON `request_token` UUID 和 `scenario`，创建或找回同一次请求的任务。 |
| `GET /api/jobs/<id>` | 读取 SQLite 中的状态、最新快照和最终结果。 |
| `GET /api/jobs/<id>/events?after=n` | 原生 SSE；终态发送 state 后关闭，缺口发送 reset，结束通知缺失时用 sync 引导重查快照。 |

先读 [service.py](service.py) 看组装和任务生命周期，再读 [storage.py](storage.py) 看事务与执行校验；[channel.py](channel.py) 和 [app.py](app.py) 实现内存尾流及 HTTP，[demo.js](static/demo.js) 实现浏览器恢复。

最多 4 个活动任务、32 个通道。这是单进程本地示例，不提供完整历史重放、断线无损、取消或生产部署保障；更多核心责任边界见 [架构](../../docs/architecture.md)。

## 测试

```sh
PYTHONPATH=src python -m unittest tests.test_demo tests.test_contract tests.test_demo_client -v
```

契约测试使用隔离子进程、临时 SQLite 与真实 localhost HTTP；已有 Node 时运行实际 `demo.js` 的受控状态单测，浏览器交互需另行验证。
