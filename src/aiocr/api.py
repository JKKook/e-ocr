"""데스크톱 앱용 API.

사용자: /chat(SSE 스트리밍) · /documents(목록·URL·업로드·삭제·쪽 미리보기) · /feedback
관리자: /search · /traces · /traces/stats · /report · /gate
인증: 환경변수 AIOCR_TOKEN 이 있으면 모든 요청에 헤더 x-aiocr-token(이미지 태그는 ?t=)이 맞아야 한다. Electron 이 실행마다 새로 만든다.
사용: AIOCR_TOKEN=... uv run uvicorn aiocr.api:app --host 127.0.0.1 --port 8765
"""
import json
import os
import queue
import secrets
import threading
import time
import uuid
from functools import lru_cache

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from aiocr import pipeline, trace
from aiocr.ingest import RAW, ingest, ingest_file, remove
from aiocr.rag import MODES, ROOT, chunk_block, client, search

R = ROOT / "reports"
TOKEN = os.environ.get("AIOCR_TOKEN", "")
app = FastAPI(title="AI OCR RAG")
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def auth(request: Request, call_next):
    if TOKEN and request.method != "OPTIONS":
        got = request.headers.get("x-aiocr-token") or request.query_params.get("t", "")
        if not secrets.compare_digest(got, TOKEN):
            return JSONResponse({"detail": "인증 토큰이 맞지 않습니다"}, status_code=401)
    return await call_next(request)


def sources() -> list[dict]:
    return json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))


@lru_cache
def via_map(engine: str) -> dict:
    """(문서, 페이지) → (추출 경로, 영상 구간 시작 초). 색인 payload 에는 없어 파싱 결과에서 읽는다."""
    rows = (json.loads(l) for f in (ROOT / "data" / "parsed" / engine).glob("*.jsonl") for l in f.open(encoding="utf-8"))
    return {(r["doc"], r["page"]): (r["via"], r.get("t0")) for r in rows}


def strip(h: dict, collection: str) -> dict:
    title = {s["id"]: s["title"] for s in sources()}
    out = {k: h.get(k) for k in ("id", "doc", "page", "context", "text", "score")}
    via, t0 = via_map(collection.split("_")[0]).get((h["doc"], h["page"]), ("", None))
    return {**out, "title": title.get(h["doc"], h["doc"]), "via": via, "t0": t0}


@app.get("/health")
def health():
    cols = [c.name for c in client().get_collections().collections]
    return {"ok": True, "collections": sorted(cols), "modes": MODES}


# ── 사용자: 대화 ─────────────────────────────────────────────

class ChatBody(BaseModel):
    q: str
    history: list[dict] = []
    docs: list[str] = []  # 검색 범위(비우면 전체)
    llm: str = "local"  # local(빠름·무료) | claude(정확)


@app.post("/chat")
def chat(b: ChatBody):
    """SSE: hits → token… → (replace) → done. 생성은 스레드에서 돌리고 이벤트를 큐로 흘린다."""
    q: queue.Queue = queue.Queue()

    def on_event(kind, data):
        if kind == "hits":
            data = {**data, "hits": [strip(h, "vlm_ctx") for h in data["hits"]]}
        q.put((kind, data))

    def work():
        try:
            pipeline.answer(b.q, b.history, llm=b.llm, on_event=on_event, docs=b.docs or None)
        except Exception as e:
            q.put(("error", f"{type(e).__name__}: {e}"))
        q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while (item := q.get()) is not None:
            yield f"event: {item[0]}\ndata: {json.dumps(item[1], ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class FeedbackBody(BaseModel):
    trace_id: int
    value: int  # 1 | -1


@app.post("/feedback")
def feedback(b: FeedbackBody):
    trace.feedback(b.trace_id, 1 if b.value > 0 else -1)
    return {"ok": True}


# ── 사용자: 문서함 ───────────────────────────────────────────

JOBS: dict[str, dict] = {}
INGEST_LOCK = threading.Lock()  # 임베딩·OCR 모델을 같이 쓰므로 한 번에 하나만


def start_job(label: str, fn) -> dict:
    jid = uuid.uuid4().hex[:8]
    job = JOBS[jid] = {"id": jid, "label": label, "state": "queued", "stage": "대기", "log": [], "t0": time.time()}

    def log(stage, msg):
        job.update(stage=stage, log=[*job["log"], {"t": round(time.time() - job["t0"], 1), "stage": stage, "msg": msg}])

    def run():
        with INGEST_LOCK:
            job["state"] = "running"
            try:
                job.update(result=fn(log), state="ok")
            except Exception as e:  # 화면에 그대로 보여 준다
                job.update(error=str(e), state="error")
            via_map.cache_clear()

    threading.Thread(target=run, daemon=True).start()
    return job


class UrlBody(BaseModel):
    url: str
    title: str = ""


@app.post("/documents/url")
def add_url(b: UrlBody):
    return start_job(b.url, lambda log: ingest(b.url.strip(), b.title.strip(), log=log))


@app.post("/documents/upload")
async def add_file(file: UploadFile = File(...), title: str = Form("")):
    data, name = await file.read(), file.filename or "upload"
    return start_job(name, lambda log: ingest_file(data, name, title.strip(), log=log))


@app.get("/jobs/{jid}")
def job_status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "없는 작업")
    return JOBS[jid]


@app.get("/documents")
def documents():
    out = []
    for s in sources():
        f = ROOT / "data" / "parsed" / "vlm" / f"{s['id']}.jsonl"
        blocks = [json.loads(l) for l in f.open(encoding="utf-8")] if f.exists() else []
        raw = next(iter(p for p in RAW.glob(f"{s['id']}.*") if not p.name.endswith(".gt.txt")), None)
        kind = "video" if raw and raw.name.endswith(".video.json") else (raw.suffix if raw else "").lstrip(".")
        out.append({**s, "pages": len(blocks), "chunks": sum(len(chunk_block(b["text"])) for b in blocks),
                    "ocr_pages": sum(b["via"].startswith("ocr") for b in blocks), "added": raw.stat().st_mtime if raw else 0,
                    "type": kind, "deletable": s["group"] == "user"})
    return out


@app.get("/documents/{sid}")
def document(sid: str):
    """읽기 화면용 — 문서 정보와 쪽(영상은 1분 구간)별 읽어 낸 글자, 쪽 이미지 여부."""
    meta = next((d for d in documents() if d["id"] == sid), None)
    if not meta:
        raise HTTPException(404, "없는 문서")
    f = ROOT / "data" / "parsed" / "vlm" / f"{sid}.jsonl"
    image = meta["type"] in ("pdf", "png", "jpg", "jpeg")
    pages = [{"page": b["page"], "via": b["via"], "t0": b.get("t0"), "text": b["text"], "image": image, "parts": b.get("parts")}
             for b in map(json.loads, f.open(encoding="utf-8"))] if f.exists() else []
    return {**meta, "pages": pages}


@app.delete("/documents/{sid}")
def delete_document(sid: str):
    with INGEST_LOCK:
        try:
            remove(sid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        via_map.cache_clear()
    return {"ok": True}


@app.get("/documents/{sid}/raw")
def raw_file(sid: str):
    """원본 파일(읽기 화면의 PDF 뷰어가 직접 그린다). 이미지 태그·PDF.js 는 헤더를 못 붙여 ?t= 토큰을 쓴다."""
    raw = next(iter(p for p in RAW.glob(f"{sid}.*") if not p.name.endswith(".gt.txt")), None)
    if not raw or raw.suffix != ".pdf":
        raise HTTPException(404, "PDF 원본 없음")
    return FileResponse(raw, media_type="application/pdf")


@app.get("/documents/{sid}/page/{page}.png")
def page_image(sid: str, page: int):
    raw = next(iter(p for p in RAW.glob(f"{sid}.*") if not p.name.endswith(".gt.txt")), None)
    if not raw:
        raise HTTPException(404, "원문 없음")
    if raw.suffix == ".pdf":
        import pymupdf
        doc = pymupdf.open(raw)
        if not 1 <= page <= len(doc):
            raise HTTPException(404, "없는 쪽")
        return Response(doc[page - 1].get_pixmap(dpi=110).tobytes("png"), media_type="image/png")
    if raw.suffix in (".png", ".jpg", ".jpeg"):
        return Response(raw.read_bytes(), media_type="image/png" if raw.suffix == ".png" else "image/jpeg")
    raise HTTPException(404, "이미지로 볼 수 없는 형식")


@app.get("/documents/{sid}/page/{page}")
def page_text(sid: str, page: int):
    f = ROOT / "data" / "parsed" / "vlm" / f"{sid}.jsonl"
    for l in f.open(encoding="utf-8") if f.exists() else []:
        b = json.loads(l)
        if b["page"] == page:
            raw = next(iter(RAW.glob(f"{sid}.*")), None)
            img = bool(raw) and raw.suffix in (".pdf", ".png", ".jpg", ".jpeg")
            return {"text": b["text"], "via": b["via"], "image": img, "t0": b.get("t0")}
    raise HTTPException(404, "없는 쪽")


# ── 보고서 모드 ─────────────────────────────────────────────

REPORT_LOCK = threading.Lock()  # 로컬 LLM 을 함께 쓰므로 보고서는 한 번에 하나


class ReportBody(BaseModel):
    docs: list[str]
    llm: str = "local"


@app.get("/reports")
def get_report_saved(docs: str):
    from aiocr.docreport import saved
    return saved(docs.split(","))


@app.post("/reports")
def make_report(b: ReportBody):
    from aiocr.docreport import build
    if not 1 <= len(b.docs) <= 4:
        raise HTTPException(400, "문서는 1~4개만 고를 수 있어요")
    jid = uuid.uuid4().hex[:8]
    job = JOBS[jid] = {"id": jid, "label": "보고서", "state": "queued", "report": None, "t0": time.time()}

    def run():
        with REPORT_LOCK:
            job["state"] = "running"
            try:
                build(b.docs, "claude" if b.llm == "claude" else "local", emit=lambda r: job.update(report=json.loads(json.dumps(r, ensure_ascii=False))))
                job["state"] = "ok"
            except Exception as e:
                job.update(error=f"{type(e).__name__}: {e}", state="error")

    threading.Thread(target=run, daemon=True).start()
    return {"id": jid, "state": "queued"}


@app.get("/reports/job/{jid}")
def report_job(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "없는 작업")
    return JOBS[jid]


class KindBody(BaseModel):
    starts: list[float]
    kind: str  # lyrics | speech | auto


@app.get("/policy/transcript")
def transcript_policy():
    from aiocr import policy
    return policy.load()


@app.post("/documents/{sid}/segments")
def set_segments(sid: str, b: KindBody):
    """영상 대본 줄을 가사/말로 고치고 그 문서만 다시 색인한다."""
    from aiocr.ingest import reindex, set_kind
    from aiocr import policy
    if b.kind != "auto" and b.kind not in {k["id"] for k in policy.load()["line_kinds"]}:
        raise HTTPException(400, "정책(policy/transcript.json)에 없는 분류")
    set_kind(sid, b.starts, b.kind)
    return start_job(f"{sid} 다시 색인", lambda log: reindex(sid, log))


@app.get("/vectors/neighbors")
def vector_neighbors(id: str, k: int = 8):
    from aiocr.docreport import neighbors
    return neighbors(id, max(1, min(20, k)))


class TopicBody(BaseModel):
    docs: list[str]
    k: int


@app.post("/reports/topics")
def report_topics(b: TopicBody):
    from aiocr.docreport import retopic
    return retopic(b.docs, max(1, min(8, b.k)))


# ── 관리자 ───────────────────────────────────────────────────

@app.get("/search")
def do_search(q: str, collection: str = "vlm_ctx", mode: str = "hybrid+rerank", k: int = 5):
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    t0 = time.time()
    hits = search(q, collection, mode, k=k)
    return {"sec": round(time.time() - t0, 2), "hits": [strip(h, collection) for h in hits]}


@app.get("/traces")
def traces(limit: int = 100):
    return trace.rows(limit)


@app.get("/traces/stats")
def trace_stats():
    return trace.stats()


@app.get("/report")
def report():
    load = lambda n: json.loads((R / n).read_text(encoding="utf-8"))
    ret = load("retrieval.json")
    for v in ret.values():
        v.pop("rows", None)
    ans = load("answers.json")
    from aiocr import scorecard
    return {"retrieval": ret, "ocr": load("ocr_eval.json"), "answers": ans["items"], "scorecard": scorecard.build()}


@app.get("/gate")
def gate():
    f = R / "gate.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
