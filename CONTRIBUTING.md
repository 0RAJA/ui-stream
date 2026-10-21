# 贡献指南

[English](CONTRIBUTING.en.md) · [文档导航](docs/README.md)

在 [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream) 提交 PR；附合成复现与相关测试。安全问题走 [私密渠道](SECURITY.md)，不要公开凭据、私人日志或业务数据。
贡献使用 MIT，保留已有声明，并确保自己有提交权限；本文不构成 CLA 或权利转让。

## 开发验证

使用 Python ≥3.11 和已启用的虚拟环境。完整测试需要已声明的 JSON/Redis extras，构建需要项目声明的 setuptools；客户端状态单测使用已有 Node，无 npm 包，缺少时明确 skip。以下命令不自动安装这些工具或依赖。

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m tools.check_docs
```

如已有 pre-commit，可运行 `pre-commit run --all-files`。核心保持零依赖，导入置顶，适配器不进入核心；修改行为应补回归，重点验证提交顺序、旧执行写入隔离、容量与异常。

CI 的质量与安全任务复用本仓库全部 pre-commit hooks，使用 Bandit 扫描 `src/ui_stream`，并通过 pip-audit 查询公开运行时依赖的已知漏洞。依赖从 metadata 的核心、`json` 和 `redis` 声明解析到隔离目录，包含传递依赖，不混入扫描工具或 ui-stream 自身；质量和安全任务成功后才验证分发。

格式与代码扫描在 runner 本地执行，不上传源码；依赖审计向 PyPI 的公开漏洞接口查询包名和版本。工具由 CI 安装固定版本；本地仅在已有工具时运行，不会因检查说明自动安装。漏洞或收集/查询失败会阻止门禁，不自动修复依赖或忽略告警。README 徽章显示远端 main / 已发布包的状态，不能替代对本地候选的验证。

## 文档与分发验证

文档中文优先，英文互链；两版命令/API/版本/行为保持一致。README 介绍用途和起步，架构解释概念与接入顺序，API 维护参数和故障语义；避免在多页重复完整契约。注释先说明做什么和为什么，必要时保留参数/返回值解释，不以缩短为目标。

`README.pypi.md` 由中文 README 转换绝对链接生成：修改后执行 `python -m tools.check_docs --write-pypi`。静态图保留同名 SVG；不要仅依赖 Mermaid。

发行扫描要求干净 clone/export，不能直接扫描含虚拟环境、缓存或 Demo 数据的开发目录。下面从**暂存区**导出受控文件（包括暂存的未提交修改）到新临时目录，再构建和验证；先确认暂存内容就是候选版本，未暂存修改不会被导出。保持虚拟环境启用，不要把它复制进导出目录。

```sh
release_source="$(mktemp -d)"
git checkout-index --all --prefix="$release_source/"
cd "$release_source"
python tools/build_local.py --output ./dist
python tools/check_release.py --project . --dist ./dist
python -m tools.verify_distribution --dist ./dist
```

`repository-allowlist.txt` 按项列出仓库文件；新增文件时同步更新。只有核心构建输入进入 `release-allowlist.txt` 和 `MANIFEST.in`；复核实际归档，禁止包含 Demo、静态资源、测试、基准及开发工具。保留严格白名单，不通过放宽规则掩盖额外文件。
