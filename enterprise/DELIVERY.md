# 企业私有化交付总览

> 定位：把 md-bridge 作为**内网可交付商品**卖出去所需的文档入口。  
> 不覆盖 SaaS 自助计费 / 支付门户（合同 + 对公即可）。

版本基线：与仓库 `backend` 版本一致（当前 **0.16.x**）。  
更新日期：2026-09-01

---

## 1. 就绪结论

| 维度 | 状态 | 说明 |
|------|------|------|
| 产品工程（转换 / 安全 / 租户 Key / License） | **可交付** | 技术四维估分见 [SCORE_93.md](./SCORE_93.md) |
| 离线镜像大包 | **待构建机完成** | 流程见 [delivery/OFFLINE_PACK.md](./delivery/OFFLINE_PACK.md) |
| 合同附件（验收 / SLA / 授权） | **模板已备** | 本目录 `delivery/` |
| SaaS 配额支付 | **不做也可卖** | 私有化用 License + 对公绕过 |

**一句话：** 工程可谈可 POC；标准「装箱商品」差的是**可复现全量镜像**与**商务签字件填空**，不是再堆功能。

---

## 2. 文档索引（按交付角色）

| 文档 | 给谁 | 用途 |
|------|------|------|
| [ACCEPTANCE.md](./ACCEPTANCE.md) | 实施 / 客户 | 合同验收清单（B1–B11） |
| [delivery/OFFLINE_PACK.md](./delivery/OFFLINE_PACK.md) | 构建方 | 离线 Docker 包怎么打、校验、移交 |
| [delivery/OPS_RUNBOOK.md](./delivery/OPS_RUNBOOK.md) | 客户运维 / 实施 | 安装、升级、备份、排障 |
| [delivery/SLA.md](./delivery/SLA.md) | 商务 / 法务 | SLA 模板（可改成合同附件） |
| [delivery/COMMERCIAL_LICENSE.md](./delivery/COMMERCIAL_LICENSE.md) | 商务 / 法务 | 商业授权书模板 |
| [delivery/PRICING_BOUNDARY.md](./delivery/PRICING_BOUNDARY.md) | 销售 / 售前 | 报价边界、加购项、不含项 |
| [LICENSE.md](./LICENSE.md) | 实施 | 一机一码签发与部署 |
| [OCR.md](./OCR.md) | 售前 / 实施 | 扫描 OCR 双引擎（可选加购） |
| [SCORE_93.md](./SCORE_93.md) | 内部 | 技术冲分与验证命令 |

脚本：

- `scripts/pack.sh` — 构建方出包  
- `scripts/install.sh` — 客户侧启动  
- `scripts/smoke_test.sh` — HTTP 冒烟  

---

## 3. 标准交付包应包含什么

1. **镜像**：`md-bridge-backend:enterprise` + `md-bridge-frontend:enterprise`（`docker save` + gzip）  
2. **编排**：`docker-compose.yml` + `.env.example`  
3. **脚本**：install / smoke /（可选）pack  
4. **文档**：本总览 + ACCEPTANCE + SLA + 商业授权 + 运维手册 + OCR/License 说明  
5. **校验**：`SHA256SUMS.txt`  
6. **License**：按客户 `machine_id` 签发的密钥（现场或出厂预置）

不含（除非合同加购）：ClamAV 二进制、GPU OCR 全量模型、客户样张定制、源码永久托管以外的二次开发。

---

## 4. 开单前检查清单（内部）

- [ ] `pytest` 验收包绿灯（见 README「打包前必过」）  
- [ ] `./enterprise/scripts/pack.sh` 产出**含镜像**的 bundle（非仅 `--docs-only`）  
- [ ] SHA256 已写入并复核  
- [ ] 客户环境：Linux x86_64、Docker 20+、磁盘（slim ≥4GB / 全量 ≥20GB）  
- [ ] `.env`：API Key /（可选）`MDBRIDGE_API_KEYS`、数据卷路径、License  
- [ ] 合同附件：ACCEPTANCE + SLA + 商业授权已填空盖章  
- [ ] 若售卖扫描 OCR：按 OCR.md 在 GPU/Linux 宿主验证 B9  

---

## 5. 与开源自托管的边界

| 项 | 开源 / 自托管 | 企业私有化交付 |
|----|----------------|----------------|
| 支持责任 | 社区尽力 | 合同 SLA |
| License | `LICENSE_REQUIRED=false` | 一机一码，可到期 |
| 模型 | 用户自备 / 可联网 | 建议镜像内 bake，内网离线 |
| 验收 | 无 | ACCEPTANCE 签字 |
| 计费 | 无 | 合同对公；非自助门户 |
