# 变更记录

[English](CHANGELOG.en.md)

## 0.1.0a4 — 写入完整性与接入契约

- **Breaking:** `finish_retry()` 更名为 `close_for_retry()`，不保留兼容别名；只尝试 checkpoint 后关闭，不自动重试或提交最终结果。
- **Breaking:** `StreamPolicy` 在构造时只接受非负内置 int；bool、int 子类、浮点数、字符串等类型及负数被拒绝，合法零值语义保留。
- **Breaking:** 配置 `state_check` 时，`iter_live` 首次迭代在任何实时读取前检查状态；终态提示仍先排空尾流。
- 拒绝同实例写重入；可能已发生写入的重入异常同时记录 DEGRADED，后续提交保留该状态，允许同线程只读回调。
- 顶层导出 Runtime 组装所需类型；统一英文诊断日志，完善异常、活动刷新、同步取消与双语使用契约。

## 0.1.0a3 — 流生命周期与归档辅助

- EventCodec 统一增加 `reset(reason)`；新 Runtime 可通知旧 publisher，传输失败返回 False 并降级，事务轮换仍由宿主负责。
- 按宿主身份去重后复用容量裁剪，分别报告追加、重复和容量丢弃；不生成身份或按正文去重。
- 同步 SSEBody 负责逐项编码与 iterator 关闭，支持注入 encoder；SQLite Demo 使用自定义协议接入，SSE 模块不再依赖 Pydantic。
- 增加质量、安全和依赖审计门禁及真实状态徽章；保留来源许可证并完善双语 API/迁移说明。

## 0.1.0a2 — 动态策略与接入文档

- `policy` 支持无参 `StreamPolicy` 工厂；每次 `send` 在锁内取一次快照，配置从下次发送生效。
- 保持固定策略与只读属性兼容；工厂错误在业务处理和写入前传播，结束路径不调用工厂。
- 新增动态阈值、活动刷新、错误及关闭路径回归；完善核心中文注释、双语契约和性能说明。

## 0.1.0a1 — 首个 alpha

- 三端口同步 Runtime：有界缓冲、checkpoint、宿主原子结果提交与 frozen summary。
- 可选 JSON/Redis profile，标准库 Counter 与 SQLite/SSE 浏览器 Demo。
- 源码、wheel/sdist、README 与敏感扫描 CI；Release 验证与独立发布模板。
- 双语图文文档与可复现的受控性能基准。

alpha API 可能变化，升级前固定版本并验证适配器契约。实际发布状态以 GitHub Releases / PyPI 为准。
