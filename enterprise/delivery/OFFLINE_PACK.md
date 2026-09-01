# 离线交付包构建与移交清单

适用角色：构建方（有网、有 Docker）  
相关脚本：`enterprise/scripts/pack.sh`

---

## 1. 两种包形态

| 形态 | 命令 | 用途 |
|------|------|------|
| **全量离线包（正式交付）** | `./enterprise/scripts/pack.sh` | 含 docker 镜像 + 文档 + 脚本 |
| **文档包（预研/无 Docker）** | `./enterprise/scripts/pack.sh --docs-only` | 仅文档脚本，**不能**代替客户侧装机 |

正式对客交付必须使用全量包。文档包仅用于内部预审或 Docker 暂不可用时的材料同步。

---

## 2. 构建机要求

- Docker 20+ 可用，`docker info` 正常  
- 磁盘：建议空闲 **≥ 40 GB**（bake docling 模型 + `docker save`）  
- 架构：默认目标 **linux/amd64**（客户机多为 x86_64）  
- 网络：构建期可访问镜像源 / Hugging Face（或已配置 `HF_ENDPOINT` 镜像）  

可选环境变量：

```bash
export MDBRIDGE_IMAGE_TAG=0.16.0          # 包名版本后缀
export BAKE_DOCLING_MODELS=1             # 1=构建时预置模型；0=运行时挂载
export HF_ENDPOINT=https://hf-mirror.com # 国内构建加速（按需）
```

ARM 构建机若要交付 x86 客户，需启用 buildx 并指定 `--platform linux/amd64`（在 compose/Dockerfile 构建参数中约定，避免客户 `exec format error`）。

---

## 3. 推荐出包步骤

```bash
# 仓库根目录
cd /path/to/md-bridge

# 1) 质量门（开发机）
cd backend && pytest -q tests/test_acceptance_pack.py tests/test_p0_commercial.py \
  tests/test_error_contract.py tests/test_tenant_jobs.py
cd ..

# 2) 构建并打包
./enterprise/scripts/pack.sh

# 3) 检查产物
ls -lh enterprise/dist/
cat enterprise/dist/SHA256SUMS.txt
```

预期产物示例：

```text
enterprise/dist/
  md-bridge-enterprise-<ver>-bundle.tar.gz    # 移交客户
  SHA256SUMS.txt
  md-bridge-enterprise-<ver>-bundle/          # 解压工作区（可选保留）
```

全量包体积通常为数 GB 级（含模型）；若只有几 KB，多半是 `--docs-only` 或镜像导出失败——**不得作为正式交付物**。

---

## 4. 移交前校验

- [ ] `tar -tzf …-bundle.tar.gz | head` 可见 `docker-compose.yml`、`scripts/`、`docs/`  
- [ ] 包内含镜像归档（如 `*.tar` / `images/`，以 pack.sh 实际布局为准）或说明客户需 `docker load` 的文件  
- [ ] `sha256sum -c SHA256SUMS.txt` 通过  
- [ ] 在**干净 Linux 机**试跑：`docker load` → `install.sh` → `smoke_test.sh`  
- [ ] 记录镜像 tag：`md-bridge-backend:enterprise` / `md-bridge-frontend:enterprise`  

---

## 5. 客户侧接收动作（摘要）

详见 [OPS_RUNBOOK.md](./OPS_RUNBOOK.md)。最短路径：

```bash
gunzip -c md-bridge-enterprise-*-bundle.tar.gz | tar -x
# 按包内说明 docker load 镜像
cp .env.example .env   # 填写 API Key / License
./scripts/install.sh
./scripts/smoke_test.sh http://127.0.0.1:8000
```

---

## 6. 常见失败与处理

| 现象 | 原因 | 处理 |
|------|------|------|
| bake 模型超时 / Hub 失败 | 网络或 Xet | `BAKE_DOCLING_MODELS=0`，运行时挂载已下载模型目录；或换 `HF_ENDPOINT` |
| `docker save` 磁盘不足 | 中间层过大 | 清理悬空镜像；加大磁盘；分卷导出 |
| docs-only 误交付 | Docker 未就绪 | 构建机修好后重打全量包 |
| 客户机 ARM / 构建机 AMD 混用 | 平台不一致 | 统一 platform 后重建 |

---

## 7. 版本与变更

每次正式出包在 `SHA256SUMS.txt` 旁保留：

- 产品版本号  
- Git commit（或发布 tag）  
- 是否含 OCR 额外层（默认企业镜像 OCR 关闭）  
- 构建日期与构建人  

变更说明可写进交付邮件或 `CHANGELOG` 摘录一页。
