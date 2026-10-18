# 贡献指南

[English](CONTRIBUTING.en.md) · [文档导航](docs/README.md)

在 [0RAJA/ui-stream](https://github.com/0RAJA/ui-stream) 提交 PR；附合成复现与相关测试。安全问题走 [私密渠道](SECURITY.md)，不要公开凭据、私人日志或业务数据。
贡献使用 MIT，保留已有声明，并确保自己有提交权限；本文不构成 CLA 或权利转让。

Python ≥3.11、虚拟环境。完整测试需要已声明的 JSON/Redis extras；客户端状态单测需要已有 Node，无 npm 包，缺少时明确 skip。

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m tools.check_docs
python tools/build_local.py --output ./dist
python tools/check_release.py --project . --dist ./dist
python -m tools.verify_distribution --dist ./dist
```

如已有 pre-commit，可运行 `pre-commit run --all-files`。核心保持零依赖，导入置顶，适配器不进入核心；修改行为应补回归，重点验证 commit/fencing/容量与异常顺序。
新增仓库文件需逐项更新 `repository-allowlist.txt`。只有核心构建输入进入 `release-allowlist.txt` 和 `MANIFEST.in`；复核实际归档，禁止包含 Demo、静态资源、测试、基准及开发工具。

文档中文优先，英文互链；两版命令/API/版本/行为保持一致。README 只留用途、起步、边界与入口，细节放 docs。
`README.pypi.md` 由中文 README 转换绝对链接生成：修改后执行 `python -m tools.check_docs --write-pypi`。静态图保留同名 SVG；不要仅依赖 Mermaid。
