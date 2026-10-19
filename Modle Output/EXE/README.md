# AduPort 私有化交付（EXE 形态）

本地文档双向转换器（PDF / DOCX / Markdown / EPUB / PPTX / …）的**私有化交付形态**——买家拿到「含 native 加速 + 一机一码 License 的离线部署包」，在自己的机器 / 服务器上离线部署，授权绑定机器指纹。

这是四种变现形态中的**主路径**：CLI（开源引流）、SDK（开发者）、MCP（AI 客户端）都是漏斗，私有化交付承载收入。

---

## 1. 技术路线：为什么不是 PyInstaller 单文件 exe

私有化交付的载体是 **Docker 离线镜像 + native wheel bundle + 一机一码 License**，而非 PyInstaller 打包的单文件可执行程序。原因：

| 因素 | 说明 |
|------|------|
| **WeasyPrint 系统库** | PDF 导出依赖 pango / cairo / gdk-pixbuf，PyInstaller 打包这些原生库易碎且不可移植 |
| **Rust 扩展** | `aduport_native._doc` 是 abi3 扩展，跨平台需按平台分别编译，塞进单文件 exe 收益低 |
| **FastAPI 动态 import** | pydantic / uvicorn 的导入机制对打包器不友好，常出现运行时 `ModuleNotFoundError` |
| **跨平台矩阵** | Docker 镜像一次构建多架构，比「每平台各打一个 exe」成本低得多 |

结论：**「EXE」= 私有化可部署形态，落地为离线 bundle + License**，而不是字面意义的 Windows 单文件。若客户坚持无 Docker 部署，走「纯 Python + native wheel」路线（`enterprise/scripts/install_native_wheel.sh`）。

---

## 2. 交付物清单

私有化交付的实体资产集中在 `enterprise/`，本目录作为**交付入口与总览**：

| 交付物 | 位置 | 用途 |
|--------|------|------|
| 离线打包脚本 | `enterprise/scripts/pack.sh` | 打包 docs + scripts + native wheels + docker 镜像 |
| 一键安装 | `enterprise/scripts/install.sh` | 客户侧安装 + 生成 secret |
| 冒烟自检 | `enterprise/scripts/smoke_test.sh` | 部署后验活 |
| 离线预检 | `enterprise/scripts/offline_preflight.sh` | 空隔环境安装前检查 |
| native wheel 安装 | `scripts/install_native_wheel.sh` | 无 Docker 纯 Python 路线 |
| native 打包/审计 | `scripts/pack_native_wheels.sh` / `audit_native_wheels.py` | 聚合 + 防 secret 泄漏审计 |
| **多平台构建矩阵** | `.github/workflows/native-wheels.yml` | Linux x64/arm64 + macOS x64/arm64 + **Windows x64（本轮新增）** |
| **签发 SOP** | `enterprise/delivery/LICENSE_ISSUANCE_SOP.md` | 一机一码标准操作流程（本轮新增） |
| 授权条款 | `enterprise/delivery/COMMERCIAL_LICENSE.md` | 对客授权边界 |
| 离线包说明 | `enterprise/delivery/OFFLINE_PACK.md` | 空隔交付打包步骤 |
| 定价边界 | `enterprise/delivery/PRICING_BOUNDARY.md` | 开源/商业分界 |
| 运维手册 | `enterprise/delivery/OPS_RUNBOOK.md` | 生产运维 |
| docker-compose | `enterprise/docker-compose.yml` | 前后端 + Redis 编排 |
| 环境模板 | `enterprise/.env.example` | License / OCR / RAG / native 全量配置 |

---

## 3. 一机一码签发（核心操作摘要）

> 完整 SOP 见 `enterprise/delivery/LICENSE_ISSUANCE_SOP.md`，此处给核心命令。

```bash
export ADUPORT_LICENSE_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# ① 客户在目标机取指纹
aduport machine-id

# ② 签发（-m 指纹 -c 客户 -s 席位 -e 到期）
aduport license-issue -m <machine_id> -c "客户名" -s 2 -e 2027-01-01

# ③ 交付前自检
aduport license-check -k "<token>"
```

`reason` 是唯一权威诊断信号：

| reason | 含义 | 处理 |
|--------|------|------|
| `ok` | 有效 | — |
| `machine_mismatch` | 指纹不匹配 | 重收 `machine-id` 重签 |
| `bad_signature` | secret 不一致 | 核对签发/部署侧 `ADUPORT_LICENSE_SECRET` |
| `expired` | 已过期 | 重签续期 |
| `missing_token` | 未配置 token | 设 `ADUPORT_LICENSE_KEY` / `LICENSE_FILE` |

---

## 4. 买家激活步骤（交付给客户）

1. 在目标机跑 `aduport machine-id`，把指纹发回我方。
2. 我方签发 token，经**独立安全通道**下发 `ADUPORT_LICENSE_SECRET`（不入邮件正文）。
3. 客户配置 `.env`：`ADUPORT_LICENSE_REQUIRED=true` + `LICENSE_SECRET` + `LICENSE_KEY`。
4. 客户跑 `aduport license-check`，确认 `valid=True reason=ok`。
5. `./enterprise/scripts/install.sh` + `smoke_test.sh` 完成部署验活。

---

## 5. 从零到发货（打包流程）

```bash
# ① CI 出全平台 native wheel bundle（含 Windows / Linux / macOS）
#    .github/workflows/native-wheels.yml（release 或 workflow_dispatch 触发）

# ② 本机打包离线 bundle（docs + scripts + wheels + docker 镜像）
./enterprise/scripts/pack.sh            # 需 Docker；无 Docker 用 --docs-only

# ③ 审计 + 校验和
python scripts/audit_native_wheels.py wheels/
cat enterprise/dist/SHA256SUMS.txt

# ④ 签发 License（见 §3 SOP）
# ⑤ 交付 + 客户自检（见 §4）
```

---

## 6. 本轮新增与变更

1. **Windows native wheel 构建**（`.github/workflows/native-wheels.yml` 新增 `windows` job）——补齐 Windows 私有化客户缺 native 加速的短板，`pack` job 已纳入 `windows` 依赖。
2. **一机一码签发 SOP**（`enterprise/delivery/LICENSE_ISSUANCE_SOP.md`）——把签发流程、密钥管理、失败排查、安全红线、批量签发脚本化。

> 已验证（本机实测）：`machine-id → license-issue → license-check` 主链路 OK；错误场景 `bad_signature / expired / machine_mismatch / missing_token` 四个 reason 均准确返回；`tests/test_license.py` 8 项全过。

---

## 7. 下一步可选

- **CI 补 Windows aarch64**（Windows on ARM 设备，当前只出 x64）。
- **License 自助门户**：买家在线填 machine-id 自动签发（替换人工 SOP）。
- **席位用量计费**：接 `ADUPORT_BILLING_WEBHOOK_URL` 做按量计费（`.env.example` 已预留）。
