# RAG / Embedding / 本地向量库

## 默认契约（外接）

`target_format=rag`（别名 `elements` / `rag_elements` / `rag-json`）导出：

- schema：`aduport.rag_elements.v1`
- `schema_version`：**1.1**（切块 + `element_id` / page / bbox）
- **不**自动调用向量库；下游可接 LangChain / 自建 ETL

Form / env：

```bash
# chunking
ADUPORT_RAG_MAX_CHARS=1200
ADUPORT_RAG_CHUNK_OVERLAP=0
# Form: rag_max_chars / rag_chunk_overlap
```

## 内置 embedding（可选 · schema 1.2）

```bash
# 默认后端：确定性 hash（无额外依赖，适合联调 / 本地检索）
ADUPORT_EMBED_BACKEND=hash
ADUPORT_EMBED_DIM=64
# 可选语义模型（需 pip install 'aduport[embed]'）
# ADUPORT_EMBED_BACKEND=fastembed
# ADUPORT_EMBED_MODEL=BAAI/bge-small-en-v1.5

# 转换时打开
# Form embed=true
# 或 ADUPORT_EMBED_DEFAULT=true（全局默认，慎用）
```

导出后每个 element 含 `embedding: float[]`，顶层：

```json
{
  "schema_version": "1.2",
  "embedding": { "backend": "hash", "model": "hashing-v1/64", "dim": 64, "count": N }
}
```

> `hash` 不是语义模型，仅供本地索引与契约联调。生产语义检索请用 `fastembed` 或外接向量库。

## 本地向量库（JSONL）

```bash
ADUPORT_VECTOR_DIR=/data/vectors
ADUPORT_VECTOR_COLLECTION=default
# Form: vector_persist=true（且 embed=true）
```

写入 `{VECTOR_DIR}/{collection}.jsonl`，按 `element_id` upsert。

检索：

```http
POST /api/vectors/search
{"query":"关键词","collection":"default","top_k":5}
```

`/api/health/detail` → `rag.embedding` / `rag.vector_store`。

## 与 OCR layout 的关系

扫描 OCR 的 `node_spans` / `page_number` / `bbox` 会进入 RAG metadata，便于带坐标检索。
版面人工校正：`POST /api/rag/layout_feedback`（见 [OCR.md](./OCR.md) 重 OCR 闭环）。
