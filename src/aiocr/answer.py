"""답변 생성·채점 — 같은 검색 결과(top-5)로 로컬 qwen2.5:7b 와 Claude Sonnet 이 답하고, Claude Opus 가 정답 대비 채점한다.

채점: correct 2 / partial 1 / wrong 0, grounded = 답의 주장이 주어진 근거에 있는가(true/false).
비용: Claude 는 claude -p 가 돌려주는 API 환산 비용(total_cost_usd), 로컬은 0 + 지연 시간.
사용: python -m aiocr.answer generate vlm_ctx  →  python -m aiocr.answer judge
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from aiocr.rag import search

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "answers.json"
SYSTEM = ("너는 문서 근거로만 답하는 사내 지식 도우미다. 아래 [근거]에 있는 내용만 써서 한국어로 1~3문장으로 답하고, "
          "끝에 사용한 근거 번호를 [1]처럼 붙여라. 근거에 답이 없으면 '근거 문서에서 찾지 못했습니다'라고만 답하라.")
CLAUDE_FLAGS = ["--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
                "--output-format", "json"]


def prompt(q: str, hits: list[dict]) -> str:
    ctx = "\n\n".join(f"[{i + 1}] ({h['doc']} p.{h['page']})\n{h['text']}" for i, h in enumerate(hits))
    return f"[근거]\n{ctx}\n\n[질문] {q}"


def ask_local(p: str) -> dict:
    body = json.dumps({"model": "qwen2.5:7b", "system": SYSTEM, "prompt": p, "stream": False,
                       "options": {"temperature": 0, "num_ctx": 8192}}).encode()
    t0 = time.time()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return {"text": r["response"].strip(), "sec": round(time.time() - t0, 2), "usd": 0.0,
            "in_tok": r.get("prompt_eval_count"), "out_tok": r.get("eval_count")}


def ask_claude(p: str, model: str = "sonnet", system: str = SYSTEM) -> dict:
    t0 = time.time()
    r = subprocess.run(["claude", "-p", "--model", model, "--system-prompt", system, *CLAUDE_FLAGS],
                       input=p, capture_output=True, text=True, timeout=600)
    j = json.loads(r.stdout)
    u = j.get("usage", {})
    return {"text": j["result"].strip(), "sec": round(time.time() - t0, 2), "usd": round(j["total_cost_usd"], 5),
            "in_tok": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
            "out_tok": u.get("output_tokens")}


def generate(collection: str) -> None:
    qs = [json.loads(l) for l in (ROOT / "eval" / "questions.jsonl").open(encoding="utf-8")]
    res = {"collection": collection, "mode": "hybrid+rerank", "k": 5, "items": []}
    for q in qs:
        hits = search(q["q"], collection, "hybrid+rerank", k=5)
        p = prompt(q["q"], hits)
        item = {"qid": q["qid"], "q": q["q"], "gold": q["answer"], "ctx_ids": [h["id"] for h in hits],
                "ctx_text": [f"[{i + 1}] {h['text']}" for i, h in enumerate(hits)],
                "local": ask_local(p), "claude": ask_claude(p)}
        res["items"].append(item)
        print(q["qid"], f"local {item['local']['sec']}s | claude {item['claude']['sec']}s ${item['claude']['usd']}", flush=True)
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))


JUDGE = ("너는 RAG 답변 채점자다. 질문·정답·근거·후보 답을 보고 JSON 한 줄만 출력한다: "
         '{"correct": 2|1|0, "grounded": true|false, "why": "<한 구절>"}. '
         "correct: 정답의 핵심 사실을 모두 맞히면 2, 일부만 맞히면 1, 틀리거나 못 찾았다고 하면 0. "
         "grounded: 후보 답의 주장이 모두 근거에 있으면 true.")


def judge() -> None:
    res = json.loads(OUT.read_text())
    for it in res["items"]:
        ctx = "\n\n".join(it.get("ctx_text", []))
        for who in ("local", "claude"):
            if "judge" in it[who]:
                continue
            p = (f"[질문] {it['q']}\n[정답] {it['gold']}\n[근거 청크 id] {', '.join(it['ctx_ids'])}\n{ctx}\n"
                 f"[후보 답] {it[who]['text']}")
            out = ask_claude(p, model="opus", system=JUDGE)["text"]
            it[who]["judge"] = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
        print(it["qid"], it["local"]["judge"]["correct"], it["claude"]["judge"]["correct"], flush=True)
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    for who in ("local", "claude"):
        xs = [it[who] for it in res["items"]]
        print(who, "score", round(sum(x["judge"]["correct"] for x in xs) / (2 * len(xs)), 3),
              "grounded", round(sum(x["judge"]["grounded"] for x in xs) / len(xs), 3),
              "sec", round(sum(x["sec"] for x in xs) / len(xs), 2), "usd_total", round(sum(x["usd"] for x in xs), 4))


def guarded(tag: str = "guarded") -> None:
    """서비스 경로(pipeline.answer: 재작성·점수 게이트·사후 검사·재생성·폴백)로 30문항을 다시 답하고 같은 채점자로 잰다.
    AIOCR_FALLBACK=0 으로 돌리면 폴백 없이. 결과: reports/answers_<tag>.json"""
    from aiocr import pipeline
    out = ROOT / "reports" / f"answers_{tag}.json"
    qs = [json.loads(l) for l in (ROOT / "eval" / "questions.jsonl").open(encoding="utf-8")]
    res = json.loads(out.read_text()) if out.exists() else {"fallback": pipeline.FALLBACK, "items": []}
    done = {it["qid"] for it in res["items"]}
    for q in qs:
        if q["qid"] in done:
            continue
        t0 = time.time()
        r = pipeline.answer(q["q"], llm="local", record=False)
        ctx = [f"[{i + 1}] {h['text']}" for i, h in enumerate(r["hits"])]
        a = {"text": r["answer"], "sec": round(time.time() - t0, 2), "usd": r["usd"], "llm": r["llm"],
             "confidence": r["confidence"], "issues": r["issues"], "retried": r["retried"]}
        p = (f"[질문] {q['q']}\n[정답] {q['answer']}\n[근거 청크 id] {', '.join(r['hit_ids'])}\n" + "\n\n".join(ctx) +
             f"\n[후보 답] {a['text']}")
        a["judge"] = json.loads(re.search(r"\{.*\}", ask_claude(p, model="opus", system=JUDGE)["text"], re.S).group(0))
        res["items"].append({"qid": q["qid"], "q": q["q"], "gold": q["answer"], "local": a})
        print(q["qid"], a["llm"], a["confidence"], a["issues"] or "", a["judge"]["correct"], a["judge"]["grounded"], flush=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    xs = [it["local"] for it in res["items"]]
    print(tag, "score", round(sum(x["judge"]["correct"] for x in xs) / (2 * len(xs)), 3),
          "grounded", round(sum(x["judge"]["grounded"] for x in xs) / len(xs), 3),
          "sec", round(sum(x["sec"] for x in xs) / len(xs), 2), "usd", round(sum(x["usd"] for x in xs), 4),
          "fallback", sum(x["llm"] != "local" for x in xs), "retried", sum(x["retried"] for x in xs))


if __name__ == "__main__":
    {"generate": generate, "judge": judge, "guarded": guarded}[sys.argv[1]](*sys.argv[2:])
