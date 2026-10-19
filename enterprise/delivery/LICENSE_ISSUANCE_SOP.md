# AduPort 企业授权签发 SOP（一机一码）

> 本文是 AduPort **内部运营文档**：把「机器指纹 → 签发 → 校验 → 交付」串成标准操作流程。
> 授权模型的技术细节见 `backend/aduport/core/license.py`；对客条款见 `COMMERCIAL_LICENSE.md`。

---

## 0. 授权模型速览

- **一机一码**：License token 绑定客户机器指纹 `machine_id`，换机器即失效（`machine_mismatch`）。
- **签名**：`HMAC-SHA256`。token = `base64url(payload).base64url(hmac_sha256(secret, payload))`。
- **签发与校验共用同一把 `ADUPORT_LICENSE_SECRET`**（≥16 字符，禁止占位符）。secret 不一致 → `bad_signature`。
- **三个 CLI 命令**：`machine-id`（取指纹）、`license-issue`（签发）、`license-check`（校验）。
- **并发席位** `seats`：超出会报 `license_seats_exceeded`（Redis 共享 / 进程内信号量兜底）。

---

## 1. 前置：签发密钥管理（最高优先级）

| 规则 | 说明 |
|------|------|
| **secret 只存密钥管理器 / 环境变量** | 禁止明文落盘、禁止写进代码 / 镜像 / git |
| **签发侧与部署侧必须同一 secret** | 不一致会导致全部 token `bad_signature` |
| **长度 ≥16，禁止占位符** | 弱 secret（`change-me`、`secret`、空）会被 `assert_license_ok` 拒绝 |
| **轮换 = 全部重签** | 换 secret 后所有已签发 token 失效，需逐一重签 |

```bash
# 生成强 secret（推荐 32 字节）
export ADUPORT_LICENSE_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

> ⚠️ 若签发时**未设置** `ADUPORT_LICENSE_SECRET` 且 `license_required=false`，程序会用**每次进程随机的临时 secret**，
> 签出的 token 换一个进程就 `bad_signature`。**签发前必须先显式 export secret。**

---

## 2. 标准签发流程（4 步）

### 第 1 步：收集客户机器指纹

让客户在**目标部署机**上执行：

```bash
aduport machine-id
# 输出示例：baba641d128d9e6206b3850882760078
```

> 指纹来源：`platform.node + uuid.getnode + /etc/machine-id(若存在)` 的 SHA-256 前 32 位。
> 容器部署必须用**持久化**的 `/etc/machine-id`（否则容器重建指纹漂移，见 §5）。

### 第 2 步：核对签约信息

在签发前确认三件事：`customer`（客户名）、`seats`（并发席位）、`expires`（到期日）。

### 第 3 步：签发

```bash
# 永久授权（无到期）
aduport license-issue -m <machine_id> -c "客户名" -s 2

# 带到期（含当日，YYYY-MM-DD）
aduport license-issue -m <machine_id> -c "客户名" -s 2 -e 2027-01-01
```

### 第 4 步：交付前自检（务必做）

把 token 发给客户前，**先在本机用同一 secret 校验**：

```bash
aduport license-check -k "<token>"
# 期望：valid=True reason=ok
```

---

## 3. 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `-m/--machine` | ✅ | 客户 machine_id（≥8 字符，过短拒签） |
| `-c/--customer` | 否 | 客户名，写入 token（席位 Redis key 用到） |
| `-s/--seats` | 否 | 并发席位，默认 1（`max(1, n)`） |
| `-e/--expires` | 否 | 到期日 `YYYY-MM-DD`，空 = 永久 |

---

## 4. 交付模板

> 发给客户的授权邮件 / 消息模板（把 `<>` 替换为实际值）：

```text
主题：AduPort 企业授权交付

您好 <客户名>，

请按以下步骤激活：

1. 确认在目标机器执行 `aduport machine-id` 输出为：
   <machine_id>
   （与授权绑定一致，不一致会提示 machine_mismatch）

2. 配置环境变量：
   ADUPORT_LICENSE_REQUIRED=true
   ADUPORT_LICENSE_SECRET=<部署侧 secret>   # 由我方安全下发，勿泄露
   ADUPORT_LICENSE_KEY=<下方 token>

3. 校验：
   aduport license-check
   # 期望 valid=True reason=ok

授权信息：席位 <seats>，到期 <expires 或 永久>。
```

> ⚠️ **secret 不要写进邮件正文**，通过独立安全通道（密码管理器 / 一次性链接）下发。

---

## 5. 失败排查表

`license-check` 的 `reason` 字段是唯一权威诊断信号：

| reason | 含义 | 处理 |
|--------|------|------|
| `ok` | 有效 | — |
| `machine_mismatch` | 机器指纹不匹配 | 客户换了机器/容器，重收 `machine-id` 重签 |
| `bad_signature` | 密钥不一致 | 签发侧与部署侧 `ADUPORT_LICENSE_SECRET` 不同 |
| `expired` | 已过期 | 重签并延长 `-e` |
| `bad_expiry` | 到期日格式错 | 用 `YYYY-MM-DD` |
| `missing_token` | 未配置 token | 设置 `ADUPORT_LICENSE_KEY` 或 `ADUPORT_LICENSE_FILE` |
| `malformed_token` | token 损坏 | 重新复制，避免换行/截断 |
| `bad_payload` | payload 非法 | token 被篡改，重签 |

### 容器指纹漂移（高频坑）

容器若用**随机临时** `/etc/machine-id`，重建后 `machine_id` 变化 → `machine_mismatch`。
解决：挂载持久卷 `-v /etc/machine-id:/etc/machine-id:ro` 或固定写入 `/etc/machine-id`。

---

## 6. 安全红线（违反即 P0）

1. **禁止**签发给未知 / 未核实的 `machine_id`。
2. **禁止**把 `ADUPORT_LICENSE_SECRET` 硬编码进代码、镜像、仓库。
3. **禁止**在生产环境 `ADUPORT_LICENSE_REQUIRED=false`。
4. **禁止**超卖 `seats`——它是并发席位硬约束，超卖会当场 `license_seats_exceeded`。
5. **禁止**把 secret 写进对客邮件正文，走独立安全通道。

---

## 7. 批量 / 脚本化签发（可选）

多客户批量签发用 Python 脚本（复用 `app.core.license.issue_license`）：

```python
import os
from aduport.core.license import issue_license

os.environ["ADUPORT_LICENSE_SECRET"] = "<安全注入，勿硬编码>"

orders = [
    ("客户A", "a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4", 2, "2027-01-01"),
    ("客户B", "e5f6a7b8e5f6a7b8e5f6a7b8e5f6a7b8", 1, ""),  # 永久
]
for customer, mid, seats, expires in orders:
    token = issue_license(machine_id_value=mid, customer=customer, seats=seats, expires=expires)
    print(f"{customer}\t{token}")
```

> 批量脚本只在**签发侧离线环境**运行，输出经加密渠道分发给各客户。
