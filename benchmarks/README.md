# Bounded synthetic benchmarks / 受控合成基准

Run from the repository root using the Python environment where the core is installed:
在仓库根目录使用已安装核心包的 Python 环境：

```bash
python -m benchmarks.run --smoke --output benchmarks/results/smoke-local.json
python -m benchmarks.run --output benchmarks/results/baseline-local.json
python -m unittest tests.test_benchmarks -v
```

The harness never installs optional packages. Existing sse-starlette can contribute an **encoding-only** adapter. Benchmark code and the HTTP/SQLite demo adapters stay outside core distributions.
脚本不会安装依赖；已安装的 sse-starlette 可参与**纯编码**比较。benchmark 和 HTTP/SQLite Demo 均不进入核心发行包。

See [English methodology](../docs/performance.en.md) / [中文方法与局限](../docs/performance.md) for workload equivalence, first-event and percentile definitions, memory/persistence accounting, resource caps and official comparison sources. [Canonical raw JSON](results/baseline-2026-10-18.json) retains each repetition and event timing. This is a small reproducible baseline, not a production-capacity claim.

The canonical JSON retains the original macOS measurements and hashes for [immutable source commit `7248096`](https://github.com/0RAJA/ui-stream/tree/72480966c9bb154362b2a378ec24eab633205f7b); subsequent Linux allocator-launch changes are not retroactively attributed to it.
canonical JSON 保留该不可变提交对应的原始 macOS 样本与摘要，后续 Linux allocator 启动策略不追溯改写历史结果。
