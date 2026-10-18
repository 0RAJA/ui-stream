# SQLite 浏览器 Demo

[English](README.en.md) · [项目首页](https://github.com/0RAJA/ui-stream)

先安装核心 wheel，再在仓库根目录运行本 Demo；Demo 不随 wheel/sdist 分发。

标准库 HTTP/SQLite + 无框架页面，数据均为合成数据。需要 macOS/Linux 的 `fcntl`；无需 Django、Redis、Node/npm 或外部服务。

```sh
python -m demos.sqlite --database ./demo.sqlite3 --port 8765 --interval 0.5
```

打开 `http://127.0.0.1:8765`，Ctrl-C 停止。`--database` 必填且须专用；同库第二进程会被拒绝，只绑定 loopback、不做反向 DNS。
刷新/恢复先读取 SQLite 最新快照，再追有界内存尾流；断开读者不取消任务。完成后显示保存结果，不重复计算。

| 场景 | 观察 |
| --- | --- |
| 正常 | 12步，每3步 checkpoint；最终快照与结果同事务保存 |
| 业务异常 | 第6步 ERROR，保存错误结果 |
| 实时失败 | 第4–7步不可用，计算/SQLite继续，DEGRADED，仍可 DONE |
| checkpoint失败 | 首次失败保留pending，后续恢复，DEGRADED |
| commit失败 | 首次失败保持open、不发finished；约1秒后重试同一Runtime |

双击创建受同步按钮状态和服务端 request_token 幂等保护；刷新优先确认未完成的创建 token。
生产者关闭而持久终态未知时停止自动 SSE 重订阅，仅保留每秒快照回查与手动恢复。
重启将遗留 RUNNING 标成 INTERRUPTED、保留已提交快照，不自动续跑。

| HTTP | 用途 |
| --- | --- |
| `POST /api/jobs` | JSON `request_token` UUID + `scenario` |
| `GET /api/jobs/<id>` | SQLite详情/快照/结果 |
| `GET /api/jobs/<id>/events?after=n` | 原生SSE；终态state后关闭，缺口reset，通知缺失sync |

最多4个活动任务、32个通道；不保证完整重放、断线无损、exactly-once、取消、跨进程实时服务或生产部署。

```sh
PYTHONPATH=src python -m unittest tests.test_demo tests.test_contract tests.test_demo_client -v
```

Demo在 -I -S 子进程、临时SQLite与真实localhost HTTP验证；Node测试是真实demo.js的受控状态单测，不能代替浏览器操作验证。
