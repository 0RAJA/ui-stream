# SQLite 浏览器 Demo

[English](README.en.md) · [项目首页](https://github.com/0RAJA/ui-stream)

标准库 HTTP/SQLite 与无框架页面，仅使用合成数据。需要 macOS/Linux 的 `fcntl`；无需 Django、Redis、Node/npm 或外部服务。

安装核心包后，在仓库根目录运行（Demo 不随 wheel/sdist 分发）：

```sh
python -m demos.sqlite --database ./demo.sqlite3 --port 8765 --interval 0.5
```

打开 `http://127.0.0.1:8765`，Ctrl-C 停止。`--database` 必填且须专用，同库第二进程会被拒绝；只绑定 loopback，无认证。

刷新/恢复先读取 SQLite 最新快照，再接有界内存尾流。读者断开不取消任务，已完成任务显示保存结果。重启将遗留 RUNNING 标为 INTERRUPTED，保留已提交快照，不自动续跑。

## 场景

| 场景 | 观察 |
| --- | --- |
| 正常 | 12 步，每 3 步 checkpoint；最终快照与结果同事务保存 |
| 业务异常 | 第 6 步 ERROR，保存错误结果 |
| 实时失败 | 第 4–7 步不可用；计算/SQLite 继续，DEGRADED，仍可 DONE |
| checkpoint 失败 | 首次失败保留 pending，后续恢复，DEGRADED |
| commit 失败 | 首次失败保持 open、不发 finished；约 1 秒后重试同一 Runtime |

重复创建由按钮状态和服务端 `request_token` 幂等保护，刷新先确认未完成的创建 token。生产者关闭而持久终态未知时，停止自动 SSE 重订阅，保留每秒快照回查和手动恢复。

## HTTP 接口与边界

| HTTP | 用途 |
| --- | --- |
| `POST /api/jobs` | JSON `request_token` UUID + `scenario` |
| `GET /api/jobs/<id>` | SQLite 详情/快照/结果 |
| `GET /api/jobs/<id>/events?after=n` | 原生 SSE；终态 state 后关闭，缺口 reset，通知缺失 sync |

最多 4 个活动任务、32 个通道。Demo 不提供完整重放、断线无损、exactly-once、取消、跨进程实时服务或生产部署保障。

## 测试

```sh
PYTHONPATH=src python -m unittest tests.test_demo tests.test_contract tests.test_demo_client -v
```

契约测试使用隔离子进程、临时 SQLite 与真实 localhost HTTP；已有 Node 时运行实际 `demo.js` 的受控状态单测，浏览器交互需另行验证。
