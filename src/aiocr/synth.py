"""합성 평가셋 — 기본 문서(평가 말뭉치 18개)마다 청크를 고르게 뽑아 Claude Sonnet 이 질문·답·근거 원문을 만든다.

검증: 근거 문구가 그 청크에 (공백 무시) 그대로 있어야 통과. 실패는 버린다.
분할: sha256(qid) 첫 바이트 % 5 == 0 → holdout(약 20%) — 가드·임계값 튜닝에 쓰지 않고 게이트 판정에만 쓴다.
사용: python -m aiocr.synth [문서당 청크 수=8]
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from aiocr.answer import ask_claude
from aiocr.collect import RAW
from aiocr.evaluate import norm
from aiocr.rag import load_chunks

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "eval" / "synth.jsonl"
SYS = ("너는 RAG 평가셋을 만드는 사람이다. 주어진 청크들 각각에 대해, 그 청크만 읽으면 답할 수 있는 질문을 하나씩 만든다. "
       "질문은 실제 사용자가 묻듯 자연스러운 한국어로 쓰되, 셋 중 하나는 '~는 뭐야?', '~ 알려줘'처럼 막연하게 써라. "
       "'이 문서', '이 청크', '위 내용' 같은 말은 쓰지 마라(검색 전에는 어느 문서인지 모른다). 고유명은 원문 표기 그대로 쓴다. "
       "evidence 는 답의 근거가 되는 원문 구절을 청크에서 **글자 그대로** 5~60자 복사한다. "
       "참고문헌 목록·표 숫자 나열뿐인 청크는 건너뛴다. JSON 배열만 출력한다: "
       '[{"i": <청크 번호>, "q": "...", "answer": "...", "evidence": "..."}]')


def src_type(doc: str) -> str:
    raw = next(iter(p for p in RAW.glob(f"{doc}.*") if not p.name.endswith(".gt.txt")))
    return "scan" if raw.name.endswith(".scan.pdf") else raw.suffix.lstrip(".")


def pick(chunks: list[dict], n: int) -> list[dict]:
    ok = [c for c in chunks if len(c["text"]) >= 250 and len(re.findall(r"\[\d+\]|et al\.", c["text"])) < 6]
    step = max(1, len(ok) / n)
    return [ok[int(i * step)] for i in range(min(n, len(ok)))]


def main(per_doc: int = 8) -> None:
    per_doc = int(per_doc)
    base = [s["id"] for s in json.loads((ROOT / "sources.json").read_text()) if s["group"] != "user"]
    chunks = load_chunks("vlm")
    rows, usd = [], 0.0
    for doc in base:
        sel = pick([c for c in chunks if c["doc"] == doc], per_doc)
        if not sel:
            continue
        body = "\n\n".join(f"[청크 {i}]\n{c['text']}" for i, c in enumerate(sel))
        r = ask_claude(body, model="sonnet", system=SYS)
        usd += r["usd"]
        try:
            items = json.loads(re.search(r"\[.*\]", r["text"], re.S).group(0))
        except (AttributeError, json.JSONDecodeError):
            print(doc, "JSON 파싱 실패, 건너뜀", flush=True)
            continue
        kept, st = 0, src_type(doc)
        for it in items:
            c = sel[it["i"]] if isinstance(it.get("i"), int) and 0 <= it["i"] < len(sel) else None
            if not c or not it.get("evidence") or norm(it["evidence"]) not in norm(c["text"]):
                continue
            qid = f"s{len(rows) + 1:03d}"
            gold = {"doc": doc, "pages": [c["page"]]} if st == "scan" else {"doc": doc, "evidence": it["evidence"]}
            split = "holdout" if hashlib.sha256(qid.encode()).digest()[0] % 5 == 0 else "dev"
            rows.append({"qid": qid, "src": st, "q": it["q"], "answer": it["answer"], "gold": gold, "split": split, "chunk": c["id"]})
            kept += 1
        print(f"{doc:22} {kept}/{len(sel)} kept  ${r['usd']:.3f}", flush=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(len(rows), "questions,", sum(r["split"] == "holdout" for r in rows), "holdout,", f"${usd:.3f}")


if __name__ == "__main__":
    main(*sys.argv[1:])
