# 来源清单

[English](PROVENANCE.en.md) · [许可证](docs/license.md)

| 类别 | 文件与来源 |
| --- | --- |
| 抽取 / 衍生 | `src/ui_stream/` 下 `__init__`、`runtime`、`types`、`exceptions`、`codec`、`archive`、`redis`、`reader`、`sse`：来自 BlueKing - Audit Center (bk-audit) 的流式模块，含提炼时新增/泛化实现，整体保守视为衍生 |
| 独立新增 | `examples/counter.py`、`demos/sqlite/`、测试、基准、打包工具、图文文档与流水线 |
| 第三方源码 | 未 vendor 库、字体或图片；依赖按 metadata 声明 |

九个衍生模块保留 `THL A29 Limited, a Tencent company` 原版权。`LICENSE.upstream` 原样保留上游根 MIT 声明，`LICENSE` 为完整 MIT 文本，`NOTICE` 记录提炼关系。

维护者已确认允许公开提炼与新增材料；此清单不重新分配版权，也不表示上游背书或商标许可。独立仓库不携带父项目历史、私有配置或数据。
