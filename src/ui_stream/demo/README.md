# 独立 SQLite Demo

本地可运行的后端与浏览器页，使用 **Python 3.11 标准库＋HTML/CSS/JS**。不需要 Django 配置、宿主模型、Redis、Celery、Node/npm 或外部服务，不需要安装依赖。macOS/Linux 使用标准库 fcntl 防止多个 Demo 进程共用同一 SQLite 文件。

发行包仍为 `ui-stream`，import 为 `ui_stream`；Demo 是同一 wheel 中的可选使用入口 `ui_stream.demo`。独立项目采用src布局，宿主通过明确版本依赖使用本包。

## 启动

安装本地候选 wheel 后使用虚拟环境（数据只写显式指定的 Demo 库）：

```bash
python -m ui_stream.demo \
  --database ./demo.sqlite3 \
  --port 8765 --interval 0.5
```

打开 `http://127.0.0.1:8765`。只绑定 `127.0.0.1`，不提供公网/生产服务。Ctrl-C 停止。手动刷新演示时可把 `--interval` 调至 `1` 或 `1.5`，更容易看到中间状态。

`--database` 必填，SQLite 文件必须专用于 Demo；同库第二进程会被拒绝，端口占用时选择另一端口。服务重启会把遗留 RUNNING 任务标成 INTERRUPTED，保留最后已提交快照，不自动续跑。

## 可重复操作

1. 选“正常完成”，点“创建演示任务”。左侧逐条显示合成进度，右侧每秒读取快照。每 3 条 checkpoint，因此快照可暂时落后实时输出。
2. 连续双击创建：按钮同步禁用；服务端相同 request_token 只创建并启动一次。任务运行期间不能重复创建。请求应答丢失后“重试创建”继续使用同 token；刷新时优先从 sessionStorage 确认尚未完成的创建请求，即使URL还保留旧任务也不会丢失新请求token。
3. 第 3 条以后刷新：URL 中的 job 标识保持不变；左侧先用最新 SQLite 快照替换，再从其游标追当前内存实时尾流。不会因为刷新创建新任务，也不会把快照当整段历史追加。
4. 点“断开实时”：左侧暂时不再收实时事件，右侧快照仍可推进，任务继续。点“恢复连接”：重新读取快照并恢复单个 SSE 连接。
5. 完成后再次刷新/恢复：直接显示 SQLite 中的终态和结果，不重新计算、不进入无限 SSE 重连。
6. 换其他故障场景并创建新任务，观察下表。刷新/切换时旧异步请求受 generation 标记限制，不覆盖新任务状态。

| 场景 | 预期行为 |
| --- | --- |
| 业务异常 | 第6步产生合成错误；当前快照、ERROR终态及错误结果在同一事务提交 |
| 实时通道不可用 | 第4–7步发布失败；计算、SQLite快照继续，完整性DEGRADED，最终结果仍可DONE |
| 中间快照失败 | 首次checkpoint失败后保留pending，下一条继续尝试并恢复；完整性DEGRADED |
| 最终提交失败 | 首次commit在写库前模拟失败，Runtime仍open/pending且不发finished；约1秒后宿主重试同一Runtime，再原子保存并发送finished |

故障注入是 Demo 声明的场景，放在自己的适配器和宿主中。SQLite 真实事务回滚另有触发器注入测试，不把“模拟失败发生在事务前”当作真实回滚的证明。

## 结构与边界

- `events.py`：独立 frozen DemoEvent/Codec，progress/finished、DONE/ERROR/INTERRUPTED；标准库JSON和SSE编码，不导入默认Pydantic Event。
- `storage.py`：SQLiteRepository、SQLiteArchiveStore与数据库进程锁。每次操作独立连接，BEGIN IMMEDIATE短事务；job/execution/RUNNING fencing。只保留最新值快照，commit同一事务保存尾部最新值、终态和结果，返回时已真实提交。
- `channel.py`：每作业有界内存deque、Condition、多读者游标；缺口返回reset。断开读者不会取消生产者。
- `service.py`：创建幂等、最多4个生产者、合成任务、受控提交重试和生命周期收敛；不继承/补丁Runtime。
- `app.py`：静态资源白名单、创建/详情/原生SSE。已终态时state后关闭；实时缺口reset；生产者已关闭而通知缺失时sync，前端回查快照；若持久终态仍未知，停止自动SSE重订阅，只保留每秒一次快照回查与手动恢复。
- `static/`：无框架单页；最多一个EventSource、一个快照轮询计时器；快照恢复替换过程状态，领域step用于忽略已显示进度。

HTTP入口：`POST /api/jobs` 的JSON为 `{request_token: UUID, scenario: 场景值}`；`GET /api/jobs/<job_id>` 读SQLite；`GET /api/jobs/<job_id>/events?after=<非负整数>` 追当前进程实时尾流。创建参数冲突409、未知作业404、非法参数400、活动任务满503。仅供本机演示，无认证/多租户。

**快照恢复不等于完整历史重放。** Demo只保存最新进度；内存尾流有界、进程结束即丢失，不承诺断线无损、事件去重/exactly-once、自动续跑、历史execution、用户取消、跨进程实时服务或多机部署。SQLite和故障开关都没有进入组件核心。

## 验证

```bash
python -m unittest tests.test_demo tests.test_contract
```

可选客户端状态回归使用本机已有Node（无需npm或安装；不影响Demo运行），专门验证旧job URL下恢复pending token，以及生产者关闭后不循环重连：

```bash
python -m unittest tests.test_demo_client
```

Demo测试在 `-I -S` 子进程中运行，关闭所有site-packages，使用临时SQLite和真实localhost HTTP，验证事务回滚/重开持久化/fencing/幂等/场景/断线/终态重连/重启收敛及静态资源；含真实SQLite持续写锁下提交及中断写入均失败但WAL快照仍可读取的情形。可设置 `UI_STREAM_TEST_PATH=/path/to/ui_stream-0.1.0a1-py3-none-any.whl` 使子进程从wheel运行；独立wheel同时包含静态资源。

上述代码测试不替代真实浏览器UI验证；维护者应在UI变化后单独记录实际操作证据。
