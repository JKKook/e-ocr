"""검색 평가 — eval/questions.jsonl 30문항으로 컬렉션×검색 방식별 Hit@1 · Recall@5 · MRR@10 을 잰다.

정답 판정: 청크가 정답 문서이고 근거 문구(공백 무시)를 담으면 정답. 스캔 문서는 OCR 오류로 문구가 깨질 수 있어
문서+페이지 일치로 판정한다(두 OCR 엔진을 같은 기준으로 비교하기 위함).
사용: python -m aiocr.evaluate rapidocr_base vlm_base ...
"""
import json
import re
import sys
import time
from pathlib import Path

from aiocr.rag import MODES, search

ROOT = Path(__file__).resolve().parents[2]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def relevant(hit: dict, gold: dict) -> bool:
    if hit["doc"] != gold["doc"]:
        return False
    if "pages" in gold:
        return hit["page"] in gold["pages"]
    return norm(gold["evidence"]) in norm(hit["text"])


def run(collection: str, mode: str, qs: list[dict]) -> dict:
    rows, t0 = [], time.time()
    for q in qs:
        hits = search(q["q"], collection, mode, k=10)
        ranks = [i for i, h in enumerate(hits) if relevant(h, q["gold"])]
        r = ranks[0] + 1 if ranks else None
        rows.append({"qid": q["qid"], "src": q["src"], "rank": r, "top": [h["id"] for h in hits[:5]]})
    n = len(rows)
    agg = lambda rs: {"hit@1": round(sum(r["rank"] == 1 for r in rs) / len(rs), 3),
                      "recall@5": round(sum(bool(r["rank"]) and r["rank"] <= 5 for r in rs) / len(rs), 3),
                      "mrr@10": round(sum(1 / r["rank"] for r in rs if r["rank"]) / len(rs), 3)}
    by_src = {s: agg([r for r in rows if r["src"] == s]) for s in sorted({r["src"] for r in rows})}
    return {"collection": collection, "mode": mode, "n": n, **agg(rows), "by_src": by_src,
            "sec_per_q": round((time.time() - t0) / n, 2), "rows": rows}


def main(*collections: str) -> None:
    qs = [json.loads(l) for l in (ROOT / "eval" / "questions.jsonl").open(encoding="utf-8")]
    out_f = ROOT / "reports" / "retrieval.json"
    results = json.loads(out_f.read_text()) if out_f.exists() else {}
    for c in collections:
        for m in MODES:
            r = run(c, m, qs)
            results[f"{c}|{m}"] = r
            print(f"{c:16} {m:14} hit@1={r['hit@1']:.3f} recall@5={r['recall@5']:.3f} mrr@10={r['mrr@10']:.3f} "
                  f"scan_r@5={r['by_src'].get('scan', {}).get('recall@5')} {r['sec_per_q']}s/q")
            out_f.write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
