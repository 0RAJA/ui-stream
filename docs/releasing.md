# 发布流程

[English](releasing.en.md) · [文档导航](README.md)

当前 Release workflow 仅验证产物；[PyPI 发布任务模板](pypi-publish-job.yml)尚未激活，未配置 PyPI secret/token。

## 验证

| 阶段 | 必需检查 |
| --- | --- |
| main / PR | Python 3.11–3.14 × Linux/macOS 源码；Linux 分发、README、文档、扫描与消费者 |
| GitHub Release | 同一套检查；tag 等于 `v` + metadata 版本，commit 属于 main |
| PyPI | 上传本轮验证产物，核对索引 metadata/摘要，并下载实际 wheel 重跑消费者 |

```sh
python -m tools.check_docs
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_local.py --output ./dist
python -m twine check --strict dist/*
python tools/check_release.py --project . --dist dist
python -m tools.verify_distribution --dist dist --check-tag v0.1.0a1
```

本 alpha 使用 `v0.1.0a1` 与 prerelease 标记。必要 CI 必须覆盖待发布 commit；创建 GitHub Release 不等于 PyPI 上传成功。

## 激活 Trusted Publisher

配置持久信任前，由维护者明确批准并登录核实 PyPI 账号：

| 项 | 值 |
| --- | --- |
| PyPI project / account | `ui-stream` / `rajawwang` |
| GitHub owner / repo | `0RAJA/ui-stream` |
| Workflow / environment | `release.yml` / `pypi` |
| OIDC | 仅 publisher job 授予 `id-token: write` |

1. 配置并回读 GitHub `pypi` environment：仅允许 `v*` tag，required reviewer 为 `0RAJA`；不支持这些保护时停止。
2. 添加 PyPI pending publisher，再将模板加入 `.github/workflows/release.yml`。pending publisher 不保留项目名，首次发布前需重新核查。
3. 由维护者在 GitHub Review deployments 批准发布；自动化不得代批或绕过。不复制 API token。

官方：[pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、[OIDC 发布](https://docs.pypi.org/trusted-publishers/using-a-publisher/)。

## 分发边界

wheel 只含 `ui_stream` 的九个运行时模块、`py.typed` 和必要元数据/许可证；最小 sdist 保留重建核心的输入与 PyPI README。Demo、示例、测试、benchmark、CI 和工具留在仓库，extras 只增加依赖。

`repository-allowlist.txt` 检查整个仓库，`release-allowlist.txt` 与 `MANIFEST.in` 限定 sdist。门禁在仓库外安装实际 wheel，加载仓库测试与 Demo、核对核心导入位置，再从 sdist 重建并逐项比较 wheel 内容。使用干净 clone/export，见[贡献指南](../CONTRIBUTING.md)。
