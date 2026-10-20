# 安全说明

[English](SECURITY.en.md) · [文档导航](docs/README.md)

优先使用 [GitHub 私密漏洞报告](https://github.com/0RAJA/ui-stream/security/advisories/new)。入口不可用时可联系维护者 `raja <rajawwang@foxmail.com>`。
报告应包含版本、影响及最小合成复现；不要将漏洞利用细节、凭据、个人信息或私有日志发到公开 issue。
这是志愿维护项目，不承诺响应 SLA。只支持最新 alpha，不承诺旧 alpha 的安全回补。

| 边界 | 需要注意 |
| --- | --- |
| 宿主 | 权限、跨进程 fencing、真实原子事务、重试/重启与保留策略 |
| Runtime | 只串行化同一 Runtime 实例的调用，不覆盖其他实例/进程；接受后的事件/嵌套值不能修改，降级异常配置应尽量窄 |
| Demo | 仅 loopback、合成数据、无认证；专用 SQLite，重启会中断而非续跑任务 |
| 发布检查 | 规则扫描与白名单不能证明不存在所有秘密、PII 或未标识的敏感含义 |

不提供公网 Demo 服务；不在仓库、包或 Actions secret 中复制账户凭据。
