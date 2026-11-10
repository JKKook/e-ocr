"""Contextual Retrieval — 청크마다 '문서 전체 속 위치'를 설명하는 짧은 맥락을 로컬 LLM(Ollama qwen2.5:7b)으로 만든다.

Anthropic 원문 프롬프트를 따르고, 같은 문서의 청크는 문서 프롬프트를 앞에 고정해 Ollama KV 캐시를 재사용한다.
결과는 (청크 텍스트 해시) 캐시에 쌓아 OCR 엔진이 달라도 같은 청크는 다시 만들지 않는다.
사용: python -m aiocr.contextualize vlm
"""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from aiocr.rag import load_chunks

ROOT = Path(__file__).resolve().parents[2]
MODEL, NUM_CTX, DOC_CAP = "qwen2.5:7b", 24576, 60000  # 문서는 앞 6만 자까지(약 1.6만 토큰)
DOC_T = "<document>\n{doc}\n</document>\n"
CHUNK_T = ("Here is the chunk we want to situate within the whole document\n<chunk>\n{chunk}\n</chunk>\n"
           "Please give a short succinct context to situate this chunk within the overall document for the purposes "
           "of improving search retrieval of the chunk. Answer only with the succinct context and nothing else. "
           "Write it in the same language as the document.")


def gen(prompt: str) -> str:
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": 120}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["response"].strip()


def main(engine: str, limit: int = 0) -> None:
    cache_f = ROOT / "data" / "context" / "cache.jsonl"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    cache = {r["key"]: r["context"] for r in map(json.loads, cache_f.open())} if cache_f.exists() else {}
    chunks = load_chunks(engine)
    docs: dict[str, str] = {}
    for f in (ROOT / "data" / "parsed" / engine).glob("*.jsonl"):
        docs[f.stem] = "\n".join(json.loads(l)["text"] for l in f.open(encoding="utf-8"))[:DOC_CAP]
    out, t0, made = [], time.time(), 0
    with cache_f.open("a", encoding="utf-8") as cw:
        for c in chunks:
            key = hashlib.sha256((docs[c["doc"]][:2000] + "\x00" + c["text"]).encode()).hexdigest()[:24]
            if key not in cache:
                cache[key] = gen(DOC_T.format(doc=docs[c["doc"]]) + CHUNK_T.format(chunk=c["text"]))
                cw.write(json.dumps({"key": key, "id": c["id"], "context": cache[key]}, ensure_ascii=False) + "\n")
                cw.flush()
                made += 1
                if made % 25 == 0:
                    print(f"{made} new, {(time.time() - t0) / made:.1f}s/chunk", flush=True)
                if limit and made >= limit:
                    return
            out.append({"id": c["id"], "context": cache[key]})
    (ROOT / "data" / "context" / f"{engine}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    print(engine, len(out), "contexts,", made, "new", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0)
