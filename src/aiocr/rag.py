"""청킹 · 색인(Qdrant dense+sparse) · 검색(dense / sparse / hybrid RRF / +rerank).

컬렉션 이름 = <ocr엔진>_<base|ctx>. ctx 는 청크 앞에 문서 맥락 문장(contextualize.py 산출)을 붙여 임베딩한다.
사용: python -m aiocr.rag index rapidocr base
"""
import json
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient, models

ROOT = Path(__file__).resolve().parents[2]
CHUNK, OVERLAP = 900, 150
MODES = ("dense", "sparse", "hybrid", "hybrid+rerank")


def chunk_block(text: str) -> list[str]:
    text = re.sub(r"-\n(?=[a-z])", "", text)            # PDF 줄끝 하이픈 이음
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) > CHUNK:
            out.append(cur)
            cur = cur[-OVERLAP:] + " " + ln             # 앞 청크 끝 150자를 겹쳐 문장 단절 완화
        else:
            cur = f"{cur} {ln}".strip() if not cur.endswith((".", "다", ":")) else f"{cur}\n{ln}"
    if cur:
        out.append(cur)
    return out


def load_chunks(engine: str) -> list[dict]:
    chunks = []
    for f in sorted((ROOT / "data" / "parsed" / engine).glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            b = json.loads(line)
            for n, c in enumerate(chunk_block(b["text"])):
                chunks.append({"id": f"{b['doc']}:{b['page']}:{n}", "doc": b["doc"], "page": b["page"], "text": c})
    return chunks


def contexts(engine: str) -> dict[str, str]:
    f = ROOT / "data" / "context" / f"{engine}.jsonl"
    return {r["id"]: r["context"] for r in map(json.loads, f.open(encoding="utf-8"))} if f.exists() else {}


@lru_cache
def embedder():
    from aiocr.embed import BgeM3
    return BgeM3()


@lru_cache
def reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(str(ROOT / "models" / "bge-reranker-v2-m3"), max_length=1024)


def client() -> QdrantClient:
    return QdrantClient(url="http://127.0.0.1:6333", timeout=120)


def index(engine: str, variant: str) -> None:
    chunks = load_chunks(engine)
    ctx = contexts(engine) if variant == "ctx" else {}
    if variant == "ctx":
        missing = [c["id"] for c in chunks if c["id"] not in ctx]
        assert not missing, f"맥락 없는 청크 {len(missing)}개 — contextualize 먼저"
    texts = [(ctx[c["id"]] + "\n\n" + c["text"]) if ctx else c["text"] for c in chunks]
    dense, sparse = embedder().encode(texts)
    qc, name = client(), f"{engine}_{variant}"
    if qc.collection_exists(name):
        qc.delete_collection(name)
    qc.create_collection(name, vectors_config={"dense": models.VectorParams(size=1024, distance=models.Distance.COSINE)},
                         sparse_vectors_config={"sparse": models.SparseVectorParams()})
    qc.upload_points(name, [
        models.PointStruct(id=i, vector={"dense": dense[i].tolist(),
                                         "sparse": models.SparseVector(indices=list(sparse[i]), values=list(sparse[i].values()))},
                           payload={**c, "context": ctx.get(c["id"], "")})
        for i, c in enumerate(chunks)], batch_size=64)
    print(name, len(chunks), "chunks")


def search(query: str, collection: str, mode: str = "hybrid", k: int = 10, pool: int = 30, timing: dict | None = None,
           docs: list[str] | None = None) -> list[dict]:
    """timing 을 넘기면 단계별 ms(embed·search·rerank)를 채운다(trace 용). docs 를 주면 그 문서 안에서만 찾는다."""
    flt = models.Filter(must=[models.FieldCondition(key="doc", match=models.MatchAny(any=docs))]) if docs else None
    t = timing if timing is not None else {}
    t0 = time.perf_counter()
    dense, sparse = embedder().encode([query])
    t["embed_ms"] = round((time.perf_counter() - t0) * 1000)
    dq = dense[0].tolist()
    sq = models.SparseVector(indices=list(sparse[0]), values=list(sparse[0].values()))
    qc = client()
    if mode == "dense":
        pts = qc.query_points(collection, query=dq, using="dense", limit=k, query_filter=flt).points
    elif mode == "sparse":
        pts = qc.query_points(collection, query=sq, using="sparse", limit=k, query_filter=flt).points
    else:
        pts = qc.query_points(collection, prefetch=[models.Prefetch(query=dq, using="dense", limit=50, filter=flt),
                                                    models.Prefetch(query=sq, using="sparse", limit=50, filter=flt)],
                              query=models.FusionQuery(fusion=models.Fusion.RRF),
                              limit=pool if mode == "hybrid+rerank" else k).points
    t["search_ms"] = round((time.perf_counter() - t0) * 1000) - t["embed_ms"]
    hits = [{**p.payload, "score": p.score} for p in pts]
    if mode == "hybrid+rerank":
        t1 = time.perf_counter()
        scores = reranker().predict([(query, (h["context"] + "\n" + h["text"]).strip()) for h in hits])
        for h, s in zip(hits, scores):
            h["score"] = float(s)
        hits = sorted(hits, key=lambda h: -h["score"])[:k]
        t["rerank_ms"] = round((time.perf_counter() - t1) * 1000)
    return hits


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "index":
        index(*rest)
    elif cmd == "search":
        for h in search(rest[0], rest[1] if len(rest) > 1 else "rapidocr_base", rest[2] if len(rest) > 2 else "hybrid"):
            print(f"{h['score']:.3f} {h['id']:24} {h['text'][:90]!r}")
