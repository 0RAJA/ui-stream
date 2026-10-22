# 发布流程

[English](releasing.en.md) · [文档导航](README.md)

CI 在首次依赖下载前用固定官方 wheel 和 SHA-256 引导 pip，发布固定到已更新 certifi 和 sigstore 的官方 Action 提交。宿主 pip、容器 pip，以及 OIDC/TUF 等使用系统 CA 的请求链路需分别核验；只更新外部 certifi 不能覆盖所有 TLS 请求。

Release workflow 在验证产物后，通过 OIDC 和受保护的 `pypi` environment 发布到 PyPI；[发布任务参考](pypi-publish-job.yml)列出权限与步骤。

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
release_tag="$(python -c 'import tomllib; from pathlib import Path; print("v" + tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])')"
python -m tools.verify_distribution --dist dist --check-tag "$release_tag"
```

标签为 `v` 加 metadata 版本；alpha 等预发行版本在 GitHub 标记为 prerelease。必要 CI 必须覆盖发布 commit，创建 GitHub Release 不等于 PyPI 上传成功。

## Trusted Publisher 配置

发布授权绑定以下账号、仓库、工作流与环境。改变这组持久信任前，必须获得维护者明确批准并登录核实：

| 项 | 值 |
| --- | --- |
| PyPI project / account | `ui-stream` / `rajawwang` |
| GitHub owner / repo | `0RAJA/ui-stream` |
| Workflow / environment | `release.yml` / `pypi` |
| OIDC | 仅 publisher job 授予 `id-token: write` |

1. 保留并回读 GitHub `pypi` environment 的保护：仅允许 `v*` tag，required reviewer 为 `0RAJA`；不支持这些保护时停止。
2. 核对现有 PyPI Trusted Publisher 与仓库、工作流和环境匹配；通过工作流 OIDC 身份发布，不引入 API token。
3. 由维护者在 GitHub Review deployments 批准发布；自动化不得代批或绕过。不复制 API token。

官方：[Trusted Publishers](https://docs.pypi.org/trusted-publishers/)、[OIDC 发布](https://docs.pypi.org/trusted-publishers/using-a-publisher/)。

## 分发边界

wheel 只含 `ui_stream` 的运行时模块及私有标准库辅助模块、`py.typed` 和必要元数据/许可证；最小 sdist 保留重建核心的输入与 PyPI README。Demo、示例、测试、benchmark、CI 和工具留在仓库，extras 只增加依赖。

`repository-allowlist.txt` 检查整个仓库，`release-allowlist.txt` 与 `MANIFEST.in` 限定 sdist。门禁在仓库外安装实际 wheel，加载仓库测试与 Demo、核对核心导入位置，再从 sdist 重建并逐项比较 wheel 内容。使用干净 clone/export，见[贡献指南](../CONTRIBUTING.md)。
