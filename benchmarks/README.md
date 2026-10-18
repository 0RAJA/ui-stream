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
