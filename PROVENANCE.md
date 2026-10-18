# 来源清单

[English](PROVENANCE.en.md) · [许可证](docs/license.md)

| 类别 | 文件与来源 |
| --- | --- |
| 抽取 / 衍生 | `src/ui_stream/` 下 `__init__`, `runtime`, `types`, `exceptions`, `codec`, `archive`, `redis`, `reader`, `sse`：来自 BlueKing - Audit Center (bk-audit) 的流式模块；包含提炼时新增/泛化实现，整体保守视为衍生 |
| 本轮新增 | `examples/counter.py`、`demos/sqlite/`、独立测试、基准、打包工具、图文文档与流水线 |
| 第三方源码 | 未 vendor 库、字体或图片；依赖按 metadata 声明 |

9个衍生模块保留 `THL A29 Limited, a Tencent company` 原版权。`LICENSE.upstream` 原样保留根 MIT 声明，`LICENSE` 为完整 MIT 文本，`NOTICE` 记录提炼关系。
维护者已确认允许公开本次提炼与新增材料；此清单记录技术来源，不重新分配版权，也不表示上游背书或商标许可。
独立仓库从审阅文件导入，不携带父项目历史、配置、环境、数据或内部交付材料。来源核查的私有执行记录不随包分发。
