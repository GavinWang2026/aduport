# anydoc（vendored in AduPort）

Rust 文档解析内核，MIT 协议。本目录为 **AduPort 内置源码**，编译为 Python 扩展 `aduport_native._doc`，由 `backend/aduport/importers/anydoc_bridge.py` 接入。

## 在本仓库中的角色

- **不进主包 PyPI**（`AduPort` 仍为纯 Python）；另发 **预编译 wheel**（GitHub Releases / `scripts/install_native_wheel.sh`）或 Docker 多阶段编译
- **快通道**：目标 = Markdown 时直出 GFM（含文本 PDF → `pdf-inspector`，见 `ADUPORT_ANYDOC_PDF_MARKDOWN`）
- **模型通道**：`to_document` → AduPort `DocumentModel` → 各导出器（PDF 无模型，永不走此通道）
- 上游 hosted OCR 分支已在 vendoring 时裁剪（私有化零外链）

## 从上游融合进本树的周边能力

| 资产 | 路径 | 用途 |
|------|------|------|
| 解析内核 | `src/` | Office / ODF / RTF / EPUB / CSV / PDF→MD |
| Python 绑定 | `python/`（fork 为 `_doc`） | 后端桥 |
| cargo-fuzz | `fuzz/` | 上传面畸形输入；`cargo +nightly fuzz run <target>` |
| GFM 回归锁 | `tests/snapshots*` + `tests/common/` | `cargo test --test snapshots` |
| 抗崩溃 smoke | `tests/robustness.rs` | 字节扰动不得 panic |

本地参考树 `anydoc-main/`（若存在）已被 `.gitignore`，**不要**覆盖本目录；有用内容经 `scripts/sync_anydoc.sh` 或手工合入。

## 构建与同步

```bash
# 从仓库根目录
bash scripts/install_native_wheel.sh --release   # 预编译（无需 Rust）
bash scripts/build_native.sh                     # 源码 maturin → backend/.venv
bash scripts/build_native.sh --prebuilt          # 先试预编译，失败再编译
bash scripts/pack_native_wheels.sh               # 打包离线 wheels tarball
bash scripts/sync_anydoc.sh                      # 半自动同步上游（见 UPSTREAM_PIN）
```

同步范围：`src/`、`python/src/*.rs`（保留 fork 的 `lib.rs`）、`fuzz/`、`tests/snapshots*`。

## 上游

- 源码真相：`native/anydoc/src/`、`native/anydoc/python/`
- 版本钉：`UPSTREAM_PIN`、`UPSTREAM_REV`
- 原始项目：[Firecrawl anydoc](https://github.com/firecrawl-dev/anydoc)（MIT）

测试夹具：`native/anydoc/tests/fixtures/` · 后端桥接测试：`backend/tests/test_anydoc_bridge.py`
