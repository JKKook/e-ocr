"""정적 데모 묶음 — 실제 API(aiocr.api)를 TestClient 로 불러 화면이 읽을 응답을 demo/data/<해시>.json 으로 떠 둔다.
데모에는 공개·개인(pms-*) 문서만 싣는다(앱에서 사용자가 추가한 문서·trace 는 제외). 원문 PDF·쪽 이미지는 직접 만든 pms-* 만.

  uv run python scripts/demo_export.py            # → demo/data/  (그 뒤 desktop: npm run build:demo)
"""
import json
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "data"

from aiocr import docreport, trace  # noqa: E402

PRESETS = [
    "CRNN 모델의 파라미터 수는 얼마인가?",
    "Which layer of CRNN converts the per-frame predictions into a label sequence?",
    "Contextual Retrieval은 검색 실패를 얼마나 줄이며, reranking을 더하면 얼마나 줄이나",
    "olmOCR로 PDF 100만 쪽을 변환하는 비용은 어느 정도인가?",
    "그린가이드 분류기를 실사용 사진 57장으로 평가한 대분류 정확도는?",
    "그린가이드 추론 서버의 콜드 스타트는 어떻게 막나?",
    "Google Cloud Vision OCR API의 1000건당 가격은 얼마인가?",  # 답 없음 → 거절
]
CURATED = ["crnn", "trocr", "contextual-retrieval", "docling-readme", "pms-gg-release", "pms-gg-classifier"]
COMPARE = [["crnn", "trocr"]]
SUMMARIZE = "이 자료의 핵심을 요약해줘"            # desktop/app/views/ChatView.tsx 와 같은 문구
NUMBERS = "이 문서에 나오는 중요한 숫자와 그 의미를 정리해줘"
COLLECTIONS = ["vlm_ctx", "vlm_base", "rapidocr_ctx", "rapidocr_base"]
MODES = ["dense", "sparse", "hybrid", "hybrid+rerank"]
dropped = {"hits": 0}


def demo_key(path: str) -> str:
    """desktop/app/lib/api.ts demoKey 와 같은 FNV-1a 32bit(UTF-16 단위)."""
    h = 0x811C9DC5
    b = path.encode("utf-16-le")
    for i in range(0, len(b), 2):
        h = ((h ^ (b[i] | b[i + 1] << 8)) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def save(path: str, obj) -> None:
    (OUT / f"{demo_key(path)}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def keep(hits: list[dict], allowed: set[str], k: int) -> list[dict]:
    out = [h for h in hits if h.get("doc") in allowed]
    dropped["hits"] += len(hits) - len(out)
    return out[:k]


def sse(text: str) -> list:
    evs = []
    for block in text.split("\n\n"):
        ev = next((l[7:] for l in block.splitlines() if l.startswith("event: ")), None)
        data = next((l[6:] for l in block.splitlines() if l.startswith("data: ")), None)
        if ev and data is not None:
            evs.append([ev, json.loads(data)])
    return evs


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    trace.DB = OUT.parent / "traces.demo.db"  # 녹화 질문의 trace 만 따로 — 사용자 trace 는 싣지 않는다
    trace.DB.unlink(missing_ok=True)
    from aiocr.api import app
    c = TestClient(app)

    def J(p, **params):
        r = c.get(p, params=params or None)
        r.raise_for_status()
        return r.json()

    docs = [d for d in J("/documents") if d["group"] != "user"]
    allowed = {d["id"] for d in docs}
    own = {i for i in allowed if i.startswith("pms-")}
    for d in docs:  # 최근 문서 카드에 대표 문서가 먼저 오게
        d["added"] = 2e9 - CURATED.index(d["id"]) if d["id"] in CURATED else d["added"] % 1e6
        d["deletable"] = False
    save("/documents", docs)
    h = J("/health")
    save("/health", h)
    print(f"문서 {len(docs)} (원문 이미지 {len(own)})", flush=True)

    for d in docs:
        det = J(f"/documents/{d['id']}")
        for p in det["pages"]:
            p["image"] = p["image"] and d["id"] in own
            pt = J(f"/documents/{d['id']}/page/{p['page']}")
            pt["image"] = pt["image"] and d["id"] in own
            save(f"/documents/{d['id']}/page/{p['page']}", pt)
            if pt["image"]:
                (OUT / f"{demo_key(f'/documents/{d['id']}/page/{p['page']}.png')}.png").write_bytes(
                    c.get(f"/documents/{d['id']}/page/{p['page']}.png").content)
        save(f"/documents/{d['id']}", {**det, "added": d["added"], "deletable": False})
        r = c.get(f"/documents/{d['id']}/raw")
        if d["id"] in own and r.status_code == 200:
            (OUT / f"{demo_key(f'/documents/{d['id']}/raw')}.pdf").write_bytes(r.content)
    save("/policy/transcript", J("/policy/transcript"))
    save("/gate", J("/gate"))
    save("/report", J("/report"))
    print("문서·관리 화면 완료", flush=True)

    # 보고서(단일·비교) + 벡터 이웃
    for ds in [[x] for x in CURATED] + COMPARE:
        if not docreport.saved(ds) or docreport.saved(ds).get("stale"):
            print("보고서 생성", ds, flush=True)
            docreport.build(ds, "local")
        rep = docreport.saved(ds)
        rep["sections"]["map"]["points"] = [p for p in rep["sections"]["map"]["points"] if p["doc"] in allowed]
        for order in {",".join(ds), ",".join(reversed(ds))}:
            save(f"/reports?docs={order}", rep)
        for p in rep["sections"]["map"]["points"]:
            if p["doc"] in ds:
                nn = J("/vectors/neighbors", id=p["id"], k=20)
                nn["items"] = keep(nn["items"], allowed, 8)
                save(f"/vectors/neighbors?id={p['id']}&k=8", nn)
    print("보고서 완료", flush=True)

    # 검색 비교
    for q in PRESETS:
        for col in COLLECTIONS:
            for m in MODES:
                r = J("/search", q=q, collection=col, mode=m, k=10)
                save(f"/search?q={q}&collection={col}&mode={m}&k=5", {**r, "hits": keep(r["hits"], allowed, 5)})
    print("검색 비교 완료", flush=True)

    # 대화 녹화 — 첫 화면 예시(로컬·Claude) + 대표 문서 빠른 버튼(로컬)
    title = {d["id"]: d["title"] for d in docs}
    runs = [(q, llm, []) for q in PRESETS for llm in ("local", "claude")]
    runs += [(q, "local", [i]) for i in CURATED for q in (SUMMARIZE, NUMBERS, SUMMARIZE.replace("이 자료", f"'{title[i]}'"))]
    for n, (q, llm, scope) in enumerate(runs, 1):
        evs = sse(c.post("/chat", json={"q": q, "history": [], "llm": llm, "docs": scope}).text)
        for ev in evs:
            if ev[0] == "hits":
                ev[1]["hits"] = keep(ev[1]["hits"], allowed, 99)
        save(f"chat|{llm}|{','.join(sorted(scope))}|{q.strip()}", evs)
        print(f"대화 {n}/{len(runs)} {llm} {q[:30]}", flush=True)
    save("/__demo/presets", PRESETS)
    save("/traces?limit=200", J("/traces?limit=200"))
    save("/traces/stats", J("/traces/stats"))
    files = list(OUT.iterdir())
    print(f"완료 · 파일 {len(files)} · {sum(f.stat().st_size for f in files) / 2**20:.1f}MB · 걸러 낸 근거 {dropped['hits']}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
