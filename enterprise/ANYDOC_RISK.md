# anydoc 依赖风险（F · 评估备忘 · v6.2）

**结论（可选战略，非本季阻断）**：anydoc 为 vendored MIT 加速层；docx 高保真路径高度依赖它，但矩阵「可读」不依赖它（真 xlsb 除外）。闭源商用前须再做法务复核，当前树内协议为 MIT + BSD。

## 事实

| 项 | 现状 |
|----|------|
| 位置 | `native/anydoc/` vendored；钉 `UPSTREAM_PIN` `v0.2.4` / SHA `42bf1c5e…` |
| 协议 | anydoc **MIT**（`anydoc_bridge.py` 头注释）；产品 **MIT** + 部分 BSD |
| 角色 | 加速层 / 部分格式领先；`registry.ensure_builtin_plugins` 保证 29 源有 Python 内置 |
| 硬依赖 | **真 .xlsb**（`xl/workbook.bin`）无 anydoc → 硬失败，合同必须标星 |
| 刻意 native-first | xlsx、pdf（OCR/docling 路径） |
| 降级 | `anydoc_fallback`：anydoc 失败回原生 importer（anydoc-only sibling 有限） |

## 风险

1. **保真不对称**：docx 视觉/结构保真在实测中主要由 anydoc 支撑；自研 OOXML 路径覆盖审计所称 ~47% 语义格——抽掉 anydoc 后「全面」叙事不成立。  
2. **供应链**：上游 tag 漂移已用 SHA pin 缓解；升级须走 `scripts/sync_anydoc.sh`，禁止跟 main 浮动。  
3. **闭源分发**：MIT 允许闭源商用，但须保留版权声明；若再链 GPL 组件则另议（当前树未发现 anydoc 侧 GPL）。  
4. **xlsb 星号**：对外「29 格式」必须写清 xlsb 依赖 anydoc。

## 建议（拍板用）

- **默认**：维持 vendored + SHA pin + 原生 fallback；xlsx/pdf 继续 native-first。  
- **合同**：标注 xlsb / anydoc 加速层 / ods/odp 浅导出。  
- **不建议**：本季为「去 anydoc」投入自研 docx 完整栈。  
- **若闭源商用**：法务确认 MIT 通知义务；不要把 anydoc 改成「自研引擎」对外表述。
