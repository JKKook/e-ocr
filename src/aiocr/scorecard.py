"""RAG 개념 채점표 — 판단(점수·감점 사유)은 eval/scorecard.json, 근거 수치는 reports/*.json 에서 매번 다시 읽는다.
채점 때 본 근거 값(seen)과 지금 값이 다르거나 기다리던 실험 결과(next.expects)가 생기면 stale 로 표시한다.

  uv run python -m aiocr.scorecard                       # 표 출력
  uv run python -m aiocr.scorecard --snapshot "<메모>"    # 재채점 후: seen 갱신 + history 한 줄
"""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
F = ROOT / "eval" / "scorecard.json"
R = ROOT / "reports"
MAX = {"impl": 4, "measure": 4, "limits": 2}


def value(path: str):
    """'파일/키/키' — 키에 . | @ 가 들어 있어 / 로 나눈다. '#count' 는 그 자리 dict·list 의 길이."""
    name, *keys = path.split("/")
    f = R / name
    if not f.exists():
        return None
    v = json.loads(f.read_text(encoding="utf-8"))
    for k in keys:
        if k == "#count":
            return len(v)
        if not isinstance(v, dict) or k not in v:
            return None
        v = v[k]
    return v


def build() -> dict:
    sc = json.loads(F.read_text(encoding="utf-8"))
    for it in sc["items"]:
        it["total"] = sum(it["score"].values())
        it["evidence"] = [{**e, "now": value(e["path"])} for e in it["evidence"]]
        changed = [e["label"] for e in it["evidence"] if "seen" in e and e["seen"] != e["now"]]
        exp = (it.get("next") or {}).get("expects")
        arrived = bool(exp) and (R / exp).exists()
        it["stale"] = changed + ([f"새 결과 {exp}"] if arrived else [])
    sc["total"] = sum(it["total"] for it in sc["items"])
    sc["max"] = 10 * len(sc["items"])
    return sc


def snapshot(note: str) -> dict:
    sc = json.loads(F.read_text(encoding="utf-8"))
    for it in sc["items"]:
        assert all(0 <= it["score"][k] <= m for k, m in MAX.items()), f"{it['id']}: 점수 범위 밖"
        for e in it["evidence"]:
            e["seen"] = value(e["path"])
    total = sum(sum(it["score"].values()) for it in sc["items"])
    sc["history"].append({"when": dt.datetime.now().isoformat(timespec="minutes"), "total": total,
                          "scores": {it["id"]: sum(it["score"].values()) for it in sc["items"]}, "note": note})
    F.write_text(json.dumps(sc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return sc


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--snapshot":
        snapshot(sys.argv[2])
    sc = build()
    for it in sc["items"]:
        s = it["score"]
        print(f"{it['term']:<26} {it['total']:>2}/10  (구현 {s['impl']} · 측정 {s['measure']} · 한계 {s['limits']})"
              + (f"  ⚠ 재채점: {', '.join(it['stale'])}" if it["stale"] else ""))
    print(f"합계 {sc['total']}/{sc['max']} · 이력 {len(sc['history'])}건")
