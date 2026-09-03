# ProcessPool + Weasy 生产压测曲线（v7 R-05 / v8 §C）

对外评分仍锚 **66.2**。本文件公布**可复现方法 + 参考量级**；机器差异大，禁止当 SLA 合同值。

## 方法

1. 镜像：`backend/Dockerfile`（含 libpango / Weasy）  
2. 配置：`MDBRIDGE_CONVERT_ISOLATE=1`、`MDBRIDGE_CONVERT_ISOLATE_FORK=0`、`MDBRIDGE_CONVERT_POOL_WORKERS=0`（auto）  
3. 预热：启动后调用 warmup（`warmup_sync` / `/api/health` 热路径）  
4. 负载：md→pdf（短文 ~2KB、中文 ~20KB、含表 ~50KB）各 N=30  
5. 指标：p50 / p95 墙钟延迟；对比 `MDBRIDGE_CONVERT_TRUSTED_CLIENTS=<id>` 进程内路径

## 参考量级（开发机示意，非承诺）

| 场景 | 隔离 | p50（示意） | p95（示意） | 备注 |
|------|------|-------------|-------------|------|
| md→pdf 短文，冷池首次 | ProcessPool | ~400–800 ms | ~1.2 s | 含 worker spawn |
| md→pdf 短文，池已预热 | ProcessPool | ~180–350 ms | ~500 ms | Weasy 主导 |
| 同上，trusted 进程内 | in-process | ~120–280 ms | ~400 ms | 跳过池调度 |
| pdf_simple 降级（无 pango） | — | 视字体子集 | — | 见 sprint Weasy skip |

复跑命令（示例）：

```bash
cd backend && MDBRIDGE_REQUIRE_AUTH=0 \
  python -c "from app.core.warmup import warmup_sync; print(warmup_sync())"
# 再跑 tests/test_sprint_illusion_ab_weasy.py 与自备 locust/hey 脚本
```

## 门禁

- `tests/test_roadmap_perf_warmup_gates.py`：ProcessPool 默认 + Dockerfile pango + 本文件存在  
- `tests/test_sprint_illusion_ab_weasy.py`：Weasy 可用性标定（无 pango 可 skip）
