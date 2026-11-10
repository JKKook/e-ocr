"""회귀 게이트 — 서비스 설정(vlm_ctx · hybrid+rerank · pool 30)으로 평가셋 전부를 돌려 기준선과 비교한다.

평가셋: 본 30문항(questions) · 합성 dev/holdout(synth) · 막연한 질문 12(vague, 정답=문서) · 답 없는 질문 12(unanswerable)
지표: 세트별 Hit@1·Recall@5·MRR@10, 답 있는 질문의 잘못된 거절률, 답 없는 질문의 거절률(guard.confidence)
판정: 기준선 대비 한 지표라도 허용폭(max(0.02, 1/n) — 한 문항 흔들림)보다 나빠지면 실패, 종료 코드 1.
사용: python -m aiocr.gate                     # 판정
      python -m aiocr.gate --baseline          # 현재 값을 기준선으로 저장
      python -m aiocr.gate --collection vlm_base --pool 10   # 설정을 바꿔 보기
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from aiocr.evaluate import relevant
from aiocr.guard import confidence
from aiocr.rag import search

ROOT = Path(__file__).resolve().parents[2]
BASE_F, OUT_F = ROOT / "reports" / "gate_baseline.json", ROOT / "reports" / "gate.json"
LOWER_BETTER = {"false_refusal"}


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (ROOT / "eval" / name).open(encoding="utf-8")]


def measure(collection: str, mode: str, pool: int) -> tuple[dict, dict]:
    sets = {"base30": load("questions.jsonl")}
    synth = load("synth.jsonl")
    sets["synth_dev"] = [q for q in synth if q["split"] == "dev"]
    sets["synth_holdout"] = [q for q in synth if q["split"] == "holdout"]
    sets["vague"] = [{**q, "gold": {"doc": q["doc"], "any": True}} for q in load("vague.jsonl")]
    un = load("unanswerable.jsonl")
    rerank = mode == "hybrid+rerank"

    metrics, n, refuse_ans, t0 = {}, {}, [], time.time()
    for name, qs in sets.items():
        ranks = []
        for q in qs:
            hits = search(q["q"], collection, mode, k=10, pool=pool)
            ok = (lambda h: h["doc"] == q["gold"]["doc"]) if q["gold"].get("any") else (lambda h: relevant(h, q["gold"]))
            rk = next((i + 1 for i, h in enumerate(hits) if ok(h)), None)
            ranks.append(rk)
            if rerank:
                refuse_ans.append(confidence(q["q"], hits[:5]) == "none")
        m = len(ranks)
        metrics[f"{name}.hit@1"] = round(sum(r == 1 for r in ranks) / m, 3)
        metrics[f"{name}.recall@5"] = round(sum(bool(r) and r <= 5 for r in ranks) / m, 3)
        metrics[f"{name}.mrr@10"] = round(sum(1 / r for r in ranks if r) / m, 3)
        for k in ("hit@1", "recall@5", "mrr@10"):
            n[f"{name}.{k}"] = m
        print(f"{name:14} n={m:3}  hit@1={metrics[f'{name}.hit@1']:.3f}  recall@5={metrics[f'{name}.recall@5']:.3f}", flush=True)
    if rerank:
        metrics["false_refusal"], n["false_refusal"] = round(sum(refuse_ans) / len(refuse_ans), 3), len(refuse_ans)
        tr = [confidence(q["q"], search(q["q"], collection, mode, k=5, pool=pool)) == "none" for q in un]
        metrics["true_refusal"], n["true_refusal"] = round(sum(tr) / len(tr), 3), len(tr)
        print(f"refusal        잘못된 거절 {metrics['false_refusal']:.3f} (n={len(refuse_ans)})  답 없는 질문 거절 {metrics['true_refusal']:.3f} (n={len(tr)})")
    metrics["sec_per_q"] = round((time.time() - t0) / (sum(len(v) for v in sets.values()) + (len(un) if rerank else 0)), 2)
    return metrics, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="vlm_ctx")
    ap.add_argument("--mode", default="hybrid+rerank")
    ap.add_argument("--pool", type=int, default=30)
    ap.add_argument("--baseline", action="store_true")
    a = ap.parse_args()
    config = {"collection": a.collection, "mode": a.mode, "pool": a.pool}
    metrics, n = measure(**config)
    when = dt.datetime.now().isoformat(timespec="seconds")
    if a.baseline:
        BASE_F.write_text(json.dumps({"when": when, "config": config, "metrics": metrics, "n": n}, ensure_ascii=False, indent=1))
        print("기준선 저장 →", BASE_F.relative_to(ROOT))
        return
    base = json.loads(BASE_F.read_text())
    checks = []
    for k, b in base["metrics"].items():
        if k == "sec_per_q" or k not in metrics:
            continue
        tol = max(0.02, 1 / n[k])
        d = round(metrics[k] - b, 3)
        bad = d > tol if k in LOWER_BETTER else d < -tol
        checks.append({"metric": k, "baseline": b, "now": metrics[k], "delta": d, "tol": round(tol, 3), "pass": not bad})
    ok = all(c["pass"] for c in checks)
    OUT_F.write_text(json.dumps({"when": when, "config": config, "baseline_when": base["when"], "pass": ok,
                                 "checks": checks, "metrics": metrics, "n": n}, ensure_ascii=False, indent=1))
    for c in checks:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['metric']:24} {c['baseline']:.3f} → {c['now']:.3f}  ({c['delta']:+.3f}, 허용 {c['tol']})")
    print("게이트", "통과" if ok else "실패", "→", f"reports/{OUT_F.name}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
