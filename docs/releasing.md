# 发布流程

[English](releasing.en.md) · [文档导航](README.md)

准备期仓库保持私有。源码 CI、双语文档与扫描通过后可恢复 public，再配置并回读验证 `v*` tag 与 `0RAJA` required reviewer 的完整环境保护。保护验证后才能配置 Trusted Publisher 和激活发布；不支持保护时停止，不降级或购买方案。

| 阶段 | 必需检查 |
| --- | --- |
| main / PR | Python 3.11–3.14 × Linux/macOS 源码；Linux 分发、README、文档、扫描与消费者 |
| GitHub Release | 同一套检查；tag 等于 `v` + metadata 版本，commit 属于 main |
| PyPI | 只使用本轮验证产物，跟进实际上传，再核对索引与下载消费者 |

```sh
python -m tools.check_docs
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_local.py --output ./dist
python -m twine check --strict dist/*
python tools/check_release.py --project . --dist dist
python -m tools.verify_distribution --dist dist --check-tag v0.1.0a1
```

## OIDC 激活前必须确认

当前 Release 仅验证；[发布任务模板](pypi-publish-job.yml)未激活。没有配置 PyPI secret/token。
以下持久信任需用户针对具体配置确认后再操作，PyPI 账号必须实际登录核实：

| 项 | 值 |
| --- | --- |
| PyPI project | `ui-stream` |
| PyPI account | `rajawwang` |
| GitHub owner / repo | `0RAJA/ui-stream` |
| Workflow | `release.yml` |
| Environment | `pypi` |
| OIDC | `id-token: write` |

GitHub `pypi` environment 必须仅允许 `v*` tag，并要求 `0RAJA` 人工审核。只在 publisher job 授予 OIDC；不复制 API token。
在已批准范围内，先配置 environment 并回读完整保护，再添加 PyPI pending publisher，最后将模板加入 `.github/workflows/release.yml`。
抵达部署审核时由维护者本人在 GitHub Review deployments 中批准；自动化不代批或绕过。
pending publisher 不保留名称，首次发布前需再次核查。

发布 alpha 时使用 `v0.1.0a1` 与 prerelease 标记；先等最新必要 CI 全绿，不能只因创建 Release 就称发布成功。
校验 PyPI 名称/版本/MIT/作者/URL/摘要，并下载真实发布 wheel 重跑消费者。
Actions 超出免费配额时先报告，不购买资源或扩权。

官方：[pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、[OIDC 发布](https://docs.pypi.org/trusted-publishers/using-a-publisher/)。

## 分发边界

默认 wheel 只含 `ui_stream` 的九个运行时模块、`py.typed` 和必要元数据/许可证。
最小 sdist 只保留重建核心所需输入与 PyPI README；Demo、示例、测试、benchmark、CI 和工具均留在仓库。
`repository-allowlist.txt` 检查整个仓库，`release-allowlist.txt` 与 `MANIFEST.in` 限定 sdist。
门禁在仓库外安装实际 wheel，加载仓库测试与宿主 Demo，核对核心导入路径，再从 sdist 重建并逐项比较 wheel 内容。
extras 只增加依赖，不控制产物文件。
