# 第三方组件与许可证声明

本文件随 AduPort 分发（wheel / Docker 镜像 / 离线包）。替代性说明：`LICENSE` 只覆盖
AduPort 自身代码（Apache-2.0），**不覆盖**下表任何组件，也不改变它们的授权。

生成依据：2026-10-19 在 `backend/.venv` 中读取各组件 `importlib.metadata` 的
`License` / `License-Expression` / classifier，以及各上游仓库的 `LICENSE` 原文。
**版本升级后须重新核对**——许可证会随版本变更。

## 1. 随发行物一起分发（wheel 运行时依赖）

| 组件 | 许可证 | 说明 |
|------|--------|------|
| anydoc（`native/anydoc/`） | **MIT** | Copyright (c) 2026 Sideguide Technologies Inc.；firecrawl/anydoc v0.2.4 的 fork，`UPSTREAM_PIN` 固定 `SHA=42bf1c5e…`。**本目录保持 MIT，不受主仓 Apache-2.0 影响。** |
| WeasyPrint | BSD-3-Clause | PDF/HTML 渲染 |
| pypdfium2 | BSD-3-Clause AND Apache-2.0 | 绑定层；其内置 PDFium 为 BSD-3-Clause（含第三方组件） |
| python-docx | MIT | DOCX 读写 |
| python-pptx | MIT | PPTX 读写 |
| markdown-it-py | MIT | Markdown 解析 |
| fonttools | MIT | 字体子集化 |
| FastAPI | MIT | HTTP 服务 |
| uvicorn | BSD-3-Clause | ASGI 服务器 |
| pydantic / pydantic-settings | MIT | 配置与模型 |
| typer | MIT | CLI |
| defusedxml | PSF-2.0 | 安全 XML 解析 |
| Pillow（可选 `[images]`） | MIT-CMU | 位图处理，仅图片路径需要 |

## 2. 可选组件（默认不随包分发）

| 组件 | 许可证 | 说明 |
|------|--------|------|
| docling（可选 `[office]`） | MIT | 深度 Office 解析 |
| MinerU（可选 `[ocr-mineru]`） | **Apache-2.0 + 附加条款** | 见下节，商用需注意的门槛与署名义务 |

### MinerU 附加条款（部署 OCR 前必读）

MinerU 采用 Apache-2.0 并附加三条约束，原文要点：

1. **商业许可门槛**：可免费商用，但若你与关联方合并口径 **MAU > 1 亿** 或
   **月总收入 > 2000 万美元**，须另行取得商业许可。
2. **在线服务署名义务**：基于 MinerU 向第三方提供在线服务时，必须在产品界面或公开文档
   显著位置说明使用了 MinerU。
3. **自动终止**：触发上述义务而未履行，许可自动终止且不另行通知。

因此：私有化部署、离线交付**无需**额外许可；若把 OCR 做成对外 SaaS，须署名。
（"MinerU 是 AGPL" 的说法不成立，请勿据此设限或据此定价。）

## 3. 明确不打包的组件

| 组件 | 许可证 | 处理 |
|------|--------|------|
| Pandoc | GPL-2.0 | **不打包**。若客户环境需要，由客户自行安装并经 CLI 调用，不与本产品形成衍生作品 |
| LibreOffice | MPL-2.0 / LGPL | **不打包进镜像**，仅作为外部桥接的可选项 |

这条是硬约束：把 GPL-2 组件放进本发行物会让整包授权失效（见 `CONTRIBUTING.md` PR 清单）。

## 4. 如果你再分发

- 保留 `LICENSE`、本文件、`NOTICE`、`native/anydoc/LICENSE` 四份原文，勿删改。
- Apache-2.0 第 4(b) 条：修改过的文件须附显著的修改说明。
- Apache-2.0 第 4(d) 条：若本产品带有 `NOTICE`，再分发时须一并提供。
