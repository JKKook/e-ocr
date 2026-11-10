"""서비스 답변 파이프라인 — 질문 재작성 → 검색 → 근거 점수 게이트 → 생성(스트리밍) → 사후 검사 → 1회 재생성 → trace.

평가 게이트(evaluate --gate)도 이 함수를 그대로 불러, 서비스와 평가가 같은 경로를 잰다.
"""
import json
import os
import re
import time
import urllib.request

from qdrant_client import models

from aiocr import guard, policy, trace
from aiocr.answer import SYSTEM, ask_claude, prompt
from aiocr.rag import client, search

OLLAMA = "http://127.0.0.1:11434/api/generate"
LOCAL = "qwen2.5:7b"
FALLBACK = os.environ.get("AIOCR_FALLBACK", "1") == "1"
REFUSAL = "문서에서 관련 내용을 찾지 못했어요. 질문을 바꿔 보시거나, 관련 문서를 문서함에 추가해 주세요."
FIX = ("\n\n[재작성 지시] 직전 답에 문제가 있었다: {why}. 근거가 영어여도 문장을 그대로 옮기지 말고 한국어 문장으로 풀어 쓰고(한자·일본어 가나 금지), "
       "근거에 그대로 나온 숫자만 쓰며, 근거에 없으면 '근거 문서에서 찾지 못했습니다'라고 답하라.")
LOW = ("\n\n[주의] 근거와 질문의 관련성이 낮다. 근거가 질문이 묻는 바로 그 대상(모델·제품·이름)을 다루지 않으면 "
       "비슷한 다른 대상으로 답하지 말고 '근거 문서에서 찾지 못했습니다'라고만 답하라.")
SUMMARY = re.compile(r"요약|정리해|줄거리|훑어|summar", re.I)
SUMMARY_SYS = ("\n\n[요약 요청] 근거는 문서 앞부터 고르게 뽑은 조각이다. 문서 전체의 핵심을 3~6개 항목(- 로 시작)으로 정리하고, "
               "항목마다 근거 번호를 붙여라. 근거에 없는 내용은 쓰지 마라.")
CONDENSE = ("아래 대화 다음에 온 마지막 질문을, 앞 대화 없이도 뜻이 통하는 한국어 검색 질문 한 문장으로 바꿔라. "
            "질문만 출력하라.\n\n{hist}\n\n[마지막 질문] {q}")


def _ollama(p: str, system: str = "", on_token=None, num_predict: int = 400) -> dict:
    body = json.dumps({"model": LOCAL, "system": system, "prompt": p, "stream": True,
                       "options": {"temperature": 0, "num_ctx": 8192, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    text, last = "", {}
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            last = json.loads(line)
            text += last.get("response", "")
            if on_token and last.get("response"):
                on_token(last["response"])
    return {"text": text.strip(), "in_tok": last.get("prompt_eval_count"), "out_tok": last.get("eval_count"), "usd": 0.0}


def condense(q: str, history: list[dict]) -> str:
    if not history:
        return q
    hist = "\n".join(f"{'사용자' if m['role'] == 'user' else '도우미'}: {m['text'][:400]}" for m in history[-4:])
    return _ollama(CONDENSE.format(hist=hist, q=q), num_predict=80)["text"].splitlines()[0].strip() or q


def generate(p: str, llm: str, system: str, on_token=None) -> dict:
    if llm == "claude":
        r = ask_claude(p, system=system)
        if on_token:
            on_token(r["text"])
        return r
    return _ollama(p, system=system, on_token=on_token)


def spread(collection: str, docs: list[str], n: int = 8) -> list[dict]:
    """범위 문서의 청크를 문서 순서대로 고르게 n개 — 요약은 검색보다 '전체를 훑는' 편이 맞다."""
    pts, _ = client().scroll(collection, scroll_filter=models.Filter(must=[
        models.FieldCondition(key="doc", match=models.MatchAny(any=docs))]), limit=2000, with_payload=True)
    order = lambda p: (docs.index(p.payload["doc"]), p.payload["page"], int(p.payload["id"].rsplit(":", 1)[1]))
    xs = sorted(pts, key=order)
    step = max(1, len(xs) / n)
    return [{**xs[int(i * step)].payload, "score": 1.0} for i in range(min(n, len(xs)))]


def answer(q: str, history: list[dict] | None = None, llm: str = "local", collection: str = "vlm_ctx",
           on_event=lambda kind, data: None, record: bool = True, docs: list[str] | None = None) -> dict:
    """docs = 사용자가 고른 검색 범위. 범위를 골랐으면 '근거 없음' 거절 대신 '확신 낮음'으로 답하고, 요약 요청은 spread() 로."""
    t0, st = time.perf_counter(), {}
    ms = lambda a: round((time.perf_counter() - a) * 1000)

    a = time.perf_counter()
    standalone = condense(q, history or [])
    st["condense_ms"] = ms(a)
    summary = bool(docs) and bool(SUMMARY.search(standalone))
    if summary:
        a = time.perf_counter()
        hits, conf = spread(collection, docs), "high"
        st["search_ms"] = ms(a)
    else:
        hits = search(standalone, collection, "hybrid+rerank", k=5, timing=st, docs=docs or None)
        conf = guard.confidence(standalone, hits)
        if docs and conf == "none" and hits:
            conf = "low"
    on_event("hits", {"hits": hits, "confidence": conf, "standalone": standalone})

    out = {"text": REFUSAL, "in_tok": 0, "out_tok": 0, "usd": 0.0}
    issues, retried = {}, False
    if conf != "none":
        a = time.perf_counter()
        p, system = prompt(standalone, hits), SYSTEM + (SUMMARY_SYS if summary else LOW if conf == "low" else "")
        if any(h.get("doc", "").startswith("video-") for h in hits):  # 영상 대본이 근거면 정책(SSOT) 규칙을 붙인다
            system += policy.rules(policy.present_in([h["text"] for h in hits]))
        out = generate(p, llm, system, on_token=lambda tok: on_event("token", tok))
        issues = guard.check_answer(out["text"], hits)
        if issues:
            retried = True
            out2 = generate(p, llm, system + FIX.format(why=json.dumps(issues, ensure_ascii=False)))
            issues2 = guard.check_answer(out2["text"], hits)
            if len(issues2) <= len(issues):
                out = {**out2, "usd": out2["usd"] + out["usd"]}
                issues = issues2
            if issues and llm == "local" and FALLBACK:  # 로컬이 두 번 다 검사에 걸리면 더 강한 모델로
                out3 = generate(p, "claude", system)
                issues3 = guard.check_answer(out3["text"], hits)
                if len(issues3) < len(issues):
                    out, issues, llm = {**out3, "usd": out3["usd"] + out["usd"]}, issues3, "local→claude"
            on_event("replace", out["text"])
        st["gen_ms"] = ms(a)
    st["total_ms"] = ms(t0)

    res = {"q": q, "standalone": standalone, "llm": llm, "collection": collection, "confidence": conf, "docs": docs or [],
           "top_score": hits[0]["score"] if hits else 0.0, "issues": issues, "retried": retried,
           "answer": out["text"], "hit_ids": [h["id"] for h in hits], "stages": st,
           "in_tok": out.get("in_tok"), "out_tok": out.get("out_tok"), "usd": out.get("usd", 0.0)}
    if record:
        res["trace_id"] = trace.record(res)
    on_event("done", res)
    return {**res, "hits": hits}
