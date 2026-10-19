# AduPort 站点（site/）

**定位：开源项目门面 + 私有化部署入口。**

⚠️ 这里**不是**照搬素材工作台那套「单页信任背书」。两者的商业形态不同，
官网该做的事也完全不同——理由见下。

---

## 为什么不能照搬素材工作台的做法

| 维度 | 素材工作台 | AduPort |
|---|---|---|
| 交付形态 | ❌ 买家要装 Python | ✅ Docker / PyPI / 离线包 |
| B 端体系 | 当天才开始做 | ✅ 已有 License / SLA / 离线包 |
| 合规敏感度 | 🔴 视频版权，在线提供即改变定性 | 🟢 用户自己的文档，零风险 |
| 开源状态 | 闭源 | MIT + PyPI |
| 架构 | 单机桌面（server.py 严禁上公网） | **原生服务端**（auth / 限流 / job 齐全） |
| 目标客户 | 泛 C 端 + 中小企业 | 律所 / 政府 / 受监管企业 |

**结论：素材工作台只能做「1 页展示站」，AduPort 能做「真·在线 Demo」。**
这是量级差别，不是程度差别。

**但有一条反直觉的硬约束**：目标客户是律所与政府，他们**绝不会**把合同上传到你的公有云。
所以——

> **在线 Demo 是获客入口，不是产品形态。**
> 它的作用是让技术评估者 30 秒验证「这东西真能转、转得准」，然后导向私有化部署。
> **绝不能做成"卖 SaaS 账号"**——那会直接劝退目标客户。
> 这一条与 `enterprise/delivery/PRICING_BOUNDARY.md` 把「公有云多租户计费门户」
> 列为可选定制而非默认项的判断一致。

---

## 站点的三个层次（当前只落地了 L1）

| 层 | 内容 | 状态 |
|---|---|---|
| **L1 · Landing** | `site/index.html` — 格式矩阵 / 架构 / 接入方式 / 私有化 / 三条路径 | ✅ 已完成 |
| **L2 · 在线 Demo** | 部署一个公开实例，上传即转 | ⏳ 待部署（见下） |
| **L3 · 文档站** | 由 `docs/` + `README.md` 生成（VitePress / Docusaurus） | ⏳ 待建 |

L2 是三者中 ROI 最高的——**因为 AduPort 有现成的 Vue3 Web UI + FastAPI 鉴权 + 限流，
部署成本几乎为零**，而它带来的信任增益远超任何文案。

---

## L1 部署前必做

| # | 动作 | 位置 |
|---|---|---|
| 1 | 替换 `YOUR_DEMO_URL` 为演示实例地址 | `index.html` 内 3 处 |
| 2 | 替换 `YOUR_EMAIL@example.com` 为商务邮箱 | `index.html` 内 3 处 |
| 3 | 替换「备案号占位」 | `index.html` 页脚 |

**价格一律不写。** `PRICING_BOUNDARY.md` 已明确「Prices and discounts are
contract-specific; this file defines scope, not list price」——本页只描述交付范围。
改动时不要擅自加价目表。

## L1 部署方式

纯静态，任意静态托管均可：

- **GitHub Pages**：仓库 Settings → Pages → 指定 `site/`，与开源项目最搭
- **Cloudflare Pages / Netlify**：拖目录即可，免费 + 自带 HTTPS
- **国内托管**：需 ICP 备案（纯展示 + 免费 Demo 属非经营性，个人主体可办）

---

## L2 在线 Demo 部署要求（重要）

Demo 是本项目独有的能力，但它是**唯一会接收外部文件的服务**，部署时必须收敛：

| 项 | 建议配置 | 原因 |
|---|---|---|
| `ADUPORT_REQUIRE_AUTH` | `1` | fail-closed，禁止裸奔 |
| 限流 | 必须开，且多 worker 要配 `ADUPORT_REDIS_URL` | 否则内存限流仅单进程有效 |
| `ADUPORT_MAX_UPLOAD_MB` | 调小（如 20） | 演示不需要 100MB |
| **OCR** | **关闭**（`ADUPORT_OCR_ENABLED=false`） | OCR 需 GPU，是被薅的主要入口 |
| `ADUPORT_JOB_TTL_SECONDS` | 调短 | 上传文件与结果都要尽快清理 |
| `ADUPORT_STREAM_CONVERT` | 视情况开 | 大文件不占内存 |
| 机器选型 | 2C4G 起，**不需要 GPU** | 常规转换是 CPU 活 |

**页面上必须写明**：「演示用途，请勿上传涉密或敏感文档」——`index.html` 已写。

### 成本量级（为什么 Demo 在 AduPort 上成立，在素材工作台上不成立）

- 常规转换（docx/pdf/md 互转）：纯 CPU，SLA 定的 p95 < 2s
- 轻量服务器 ¥60–100/月可承担大量转换，**单次边际成本约 ¥0.002–0.004**
- 对比素材工作台的视频转码服务端方案：**单次 0.45–0.9 元**（带宽主导）
- → 差 **150–300 倍**。这是"能不能做在线"的分水岭

---

## 🔴 红线

- **不写价格** —— 企业版为合同议价，页面上只描述 scope
- **不做公有云 SaaS 卖账号** —— 与目标客户（律所/政府）的需求直接冲突
- **Demo 必须限流 + 关 OCR + 短期 TTL** —— 否则会被薅
- **不承诺做不到的事** —— 页面上所有数字（36/18/1184/p95/99.5%）均取自
  `README.md` 与 `enterprise/delivery/SLA.md`，改动时保持同源

## 本地预览

```bash
cd site && python3 -m http.server 8080
```

或直接双击 `index.html`。
