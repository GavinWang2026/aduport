# anydoc（vendored in md-bridge）

Rust 文档解析内核，MIT 协议。本目录为 **md-bridge 内置源码**，编译为 Python 扩展 `mdbridge_native._doc`，由 `backend/app/importers/anydoc_bridge.py` 接入。

## 在本仓库中的角色

- **不是**独立 PyPI 依赖；Docker 镜像默认多阶段编译安装
- **快通道**：目标 = Markdown 时直出 GFM
- **模型通道**：`to_document` → md-bridge `DocumentModel` → 各导出器
- 上游 hosted OCR 分支已在 vendoring 时裁剪（私有化零外链）

## 构建与同步

```bash
# 从仓库根目录
bash scripts/build_native.sh          # maturin → backend/.venv
bash scripts/sync_anydoc.sh           # 半自动同步上游（见 UPSTREAM_PIN）
```

## 上游

- 源码真相：`native/anydoc/src/`、`native/anydoc/python/`
- 版本钉：`UPSTREAM_PIN`、`UPSTREAM_REV`
- 原始项目：[Firecrawl anydoc](https://github.com/firecrawl-dev/anydoc)（MIT）

测试夹具：`native/anydoc/tests/fixtures/` · 后端桥接测试：`backend/tests/test_anydoc_bridge.py`
