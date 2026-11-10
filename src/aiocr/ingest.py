"""URL 하나 증분 색인 — 수집(wiki/raw·sources.json·index.md·log.md) → 파싱(엔진 2) → 청킹 → 맥락 → 임베딩 → 기존 4 색인에 upsert.

전체 재색인(rag index)은 하지 않는다. 맥락은 data/context/<engine>.jsonl 에도 덧붙여 나중의 전체 재색인과 어긋나지 않게 한다.
사용: python -m aiocr.ingest <url> [제목]
"""
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from qdrant_client import models

from aiocr.collect import RAW
from aiocr.contextualize import CHUNK_T, DOC_CAP, DOC_T, gen
from aiocr import policy
from aiocr.guard import MAX_BYTES, safe_fetch
from aiocr.parse import ocr_rapid, ocr_vlm, parse_html, parse_pdf, parse_pptx
from aiocr.rag import chunk_block, client, embedder

ROOT = Path(__file__).resolve().parents[2]
ENGINES = {"vlm": ocr_vlm, "rapidocr": ocr_rapid}


GENERIC = {"readme", "index", "main", "master", "blob", "raw", "pdf", "abs", "html", "docs", "wiki", "en", "ko"}


def slug(url: str, taken: set[str]) -> str:
    """사이트 이름 + 경로에서 뜻 있는 마지막 조각 — raw.githubusercontent.com/tesseract-ocr/tesseract/main/README.md → githubusercontent-tesseract"""
    u = urlparse(url)
    if is_video(url):  # youtube.com/watch?v=ID · youtu.be/ID · /shorts/ID
        vid = parse_qs(u.query).get("v", [""])[0] or Path(u.path).name
        return _unique(re.sub(r"[^a-z0-9]+", "-", f"video {vid}".lower()).strip("-"), taken)
    segs = [re.sub(r"\.[a-z][a-z0-9]{1,4}$", "", p, flags=re.I) for p in u.path.split("/") if p]
    segs = [p for p in segs if p.lower() not in GENERIC]
    host = u.netloc.split(".")[-2] if u.netloc.count(".") else u.netloc
    last = re.sub(r"[^a-z0-9]+", "-", (segs[-1] if segs else "").lower()).strip("-")
    if segs and not last:  # 한글만 있는 파일명 등
        last = hashlib.sha256(url.encode()).hexdigest()[:6]
    return _unique(re.sub(r"[^a-z0-9]+", "-", f"{host} {last}".lower()).strip("-")[:40], taken)


def _unique(base: str, taken: set[str]) -> str:
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}-{n}", n + 1
    return sid


def title_of(path: Path, url: str) -> str:
    if path.suffix == ".pdf":
        import pymupdf
        doc = pymupdf.open(path)
        meta = ((doc.metadata or {}).get("title") or "").strip()
        if len(meta) > 8 and not re.fullmatch(r"[\d.v]+|untitled|microsoft word.*", meta, re.I):
            return meta
        # 메타데이터 제목이 없거나 arXiv 번호뿐이면 첫 쪽에서 가장 큰 글씨 줄을 제목으로
        spans = [sp for b in doc[0].get_text("dict")["blocks"] for l in b.get("lines", []) for sp in l["spans"]
                 if sp["text"].strip() and not sp["text"].strip().startswith("arXiv:")]  # 옆 여백의 arXiv 표시 제외
        if spans:
            big = max(sp["size"] for sp in spans)
            t = " ".join(sp["text"].strip() for sp in spans if sp["size"] >= big - 0.5)
            if len(t) > 8:
                return re.sub(r"\s+", " ", t)[:160]
        return meta or Path(urlparse(url).path).name or url
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^#\s+(.+)$", text, re.M) if path.suffix == ".md" else re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else url


EXTS = {".pdf", ".pptx", ".html", ".htm", ".md", ".txt", ".png", ".jpg", ".jpeg"}


def existing(**key) -> dict | None:
    """같은 주소·같은 내용이 이미 있으면 다시 수집하지 않고 그 문서를 돌려준다(사용자는 그대로 질문하면 된다)."""
    (k, v), = key.items()
    src = json.loads((ROOT / "sources.json").read_text())
    s = next((s for s in src if s.get(k) == v), None)
    if not s and k == "sha256":  # 주소로 받은 문서는 해시가 기록에 없을 수 있어 원문 파일과 직접 비교
        for x in src:
            raw = next(iter(p for p in RAW.glob(f"{x['id']}.*") if not p.name.endswith(".gt.txt")), None)
            if raw and hashlib.sha256(raw.read_bytes()).hexdigest() == v:
                s = x
                break
    return {"id": s["id"], "title": s["title"], "chunks": {}, "existing": True} if s else None


def ingest(url: str, title: str = "", log=print) -> dict:
    """URL 수집 — 내부·사설 주소와 30MB 초과는 guard.safe_fetch 가 막는다."""
    if hit := existing(url=url):
        return hit
    if is_video(url):
        return ingest_video(url, title, log)
    log("수집", f"{url} 받는 중")
    data = safe_fetch(url)
    ext = ".pdf" if data[:5] == b"%PDF-" else ".md" if url.endswith(".md") else ".html"
    return _ingest(data, ext, slug_hint=url, origin={"kind": "pdf" if ext == ".pdf" else "url", "url": url}, title=title, log=log)


def ingest_file(data: bytes, filename: str, title: str = "", log=print) -> dict:
    """업로드 파일 수집 — 같은 내용(sha256)이 이미 있으면 거절한다."""
    ext = Path(filename).suffix.lower()
    if ext not in EXTS:
        raise ValueError(f"지원하지 않는 형식이에요({ext or '확장자 없음'}). 지원: {', '.join(sorted(EXTS))}")
    if len(data) > MAX_BYTES:
        raise ValueError(f"{MAX_BYTES // 2**20}MB 를 넘는 파일은 받지 않아요")
    sha = hashlib.sha256(data).hexdigest()
    if hit := existing(sha256=sha):
        return hit
    log("수집", f"{filename} 업로드 ({len(data):,} B)")
    return _ingest(data, ext, slug_hint=f"https://upload/{filename}", origin={"kind": "file", "path": f"upload:{filename}", "sha256": sha},
                   title=title or ("" if ext == ".pdf" else Path(filename).stem), log=log)  # PDF 는 첫 쪽에서 제목을 뽑는다


def _parse(raw: Path, ocr) -> list[dict]:
    if raw.name.endswith(".video.json"):
        return windows(json.loads(raw.read_text(encoding="utf-8")))
    ext = raw.suffix
    if ext == ".pdf":
        return parse_pdf(raw, ocr)
    if ext in (".html", ".htm"):
        return parse_html(raw)
    if ext == ".pptx":
        return parse_pptx(raw)
    if ext in (".png", ".jpg", ".jpeg"):
        t0 = time.time()
        return [{"page": 1, "text": ocr(raw.read_bytes()), "via": ocr.__name__, "ocr_sec": round(time.time() - t0, 2)}]
    return [{"page": 1, "text": raw.read_text(encoding="utf-8", errors="ignore"), "via": "md"}]


VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")
WINDOW = 60  # 초 — 대본을 이 길이 구간으로 묶어 '쪽' 대신 쓴다
STT_MODEL = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"  # HF 캐시에 있는 것만 쓴다(local_files_only)
HALLUCINATION = re.compile(r"^(thank you for watching|시청해 주셔서 감사합니다|구독과 좋아요)[.!\s]*$", re.I)


def is_video(url: str) -> bool:
    host = (urlparse(url).hostname or "").removeprefix("www.").removeprefix("m.")
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


def _vtt(text: str) -> list[dict]:
    """WebVTT → [{start, text}] — 자동 자막의 겹쳐 흐르는 줄은 앞 줄과 같으면 버린다."""
    segs, last = [], ""
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"(\d+):(\d\d):(\d\d)[.,]\d+\s+-->", block) or re.search(r"(\d\d):(\d\d)[.,]\d+\s+-->", block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] if len(g) == 3 else g[0] * 60 + g[1]
        lines = [html.unescape(re.sub(r"<[^>]+>", "", l)).replace(">>", "").strip() for l in block.splitlines()[1:] if "-->" not in l]
        for line in filter(None, lines):
            if line != last:
                segs.append({"start": start, "text": line})
                last = line
    return segs


def label_transcript(v: dict, log=print) -> None:
    """정책(policy/transcript.json)대로 줄마다 분류 — speech·lyrics·foreign·cue + 줄 안의 고유명(proper)·자막 오류 의심(garbled).
    LLM 은 기본값이 아닌 줄만 JSON 으로 돌려주고, 원문에 없는 고유명·오류 원문은 버린다(줄 내용은 절대 바꾸지 않음).
    사용자가 고친 줄(overrides)이 이긴다."""
    P, ks = policy.load(), policy.kinds()
    segs, cue = v["segments"], policy.cue_re()
    for sg in segs:
        sg.update(kind="cue" if cue.match(sg["text"]) else "speech", proper=[], garbled=[])
    mus = [sg["start"] for sg in segs if re.search(r"\[(음악|Music|music|노래)\]|♪", sg["text"])]
    near = lambda t: sum(abs(t - m) <= P["detector"]["music_window_sec"] for m in mus) >= 2
    idx = [i for i, sg in enumerate(segs) if sg["kind"] != "cue"]
    D = P["detector"]
    use_claude = D.get("model") == "claude"
    B = D["batch_claude"] if use_claude else D["batch"]
    C = D.get("checks", {})
    batches = [idx[i:i + B] for i in range(0, len(idx), B)]
    for bi, batch in enumerate(batches):
        lines = "\n".join(f"{n + 1}. ({int(segs[i]['start']) // 60}:{int(segs[i]['start']) % 60:02d}){' (음악 표시 근처)' if near(segs[i]['start']) else ''} "
                          f"{re.sub(r'\[[^\]]{1,8}\]', '', segs[i]['text']).strip()}" for n, i in enumerate(batch))
        if use_claude:
            from aiocr.answer import ask_claude
            out = ask_claude(lines, model="sonnet", system=D["prompt"])["text"]
        else:
            out = gen(D["prompt"] + "\n\n" + lines)
        for x in _objs(out):
            n = x.get("i")
            if not isinstance(n, int) or not 1 <= n <= len(batch):
                continue
            sg = segs[batch[n - 1]]
            latin = len(re.findall(r"[A-Za-z]", sg["text"])) / max(1, len(re.findall(r"[A-Za-z가-힣]", sg["text"])))
            if x.get("kind") == "lyrics" or (x.get("kind") == "foreign" and latin >= C.get("foreign_min_latin_ratio", 0.5)):
                sg["kind"] = x["kind"]
            sg["proper"] = [p for p in x.get("proper", []) if isinstance(p, str) and p and p in sg["text"]
                            and len(p) <= C.get("proper_max_chars", 20) and not re.search(r"[.?!,。]", p)][:6]
            script = lambda t: "ko" if re.search(r"[가-힣]", t) else "en"
            sg["garbled"] = [g for g in x.get("garbled", []) if isinstance(g, dict) and g.get("orig") and g["orig"] in sg["text"]
                             and g.get("guess") and g["guess"] != g["orig"] and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", g["guess"])
                             and (not C.get("garbled_same_script", True) or script(g["orig"]) == script(g["guess"]))  # 추정이 번역이면 버린다
                             and len(g["guess"]) <= 2 * len(g["orig"]) + 4][:3]
        # 외국어 문장은 글자 비율로도 잡는다(LLM 이 놓쳐도)
        for i in batch:
            t = segs[i]["text"]
            if segs[i]["kind"] == "speech" and len(re.findall(r"[A-Za-z]", t)) >= 10 and len(re.findall(r"[A-Za-z]", t)) > 2 * len(re.findall(r"[가-힣]", t)):
                segs[i]["kind"] = "foreign"
        log("파싱", f"대본 분류 {bi + 1}/{len(batches)}")
    for sg in segs:
        sg["auto_kind"] = sg["kind"]  # '자동 판별로' 되돌릴 값 — 아래 override 가 kind 를 덮어도 남는다
        k = v.get("overrides", {}).get(f"{sg['start']:.2f}")
        if k in ks:
            sg["kind"] = k
    v["policy_version"] = P["version"]


def _objs(text: str) -> list[dict]:
    """LLM 출력에서 온전한 JSON 객체만(배열이 중간에 깨져도)."""
    dec, out, i = json.JSONDecoder(), [], 0
    while (i := text.find("{", i)) >= 0:
        try:
            o, end = dec.raw_decode(text[i:])
            if isinstance(o, dict):
                out.append(o)
            i += end
        except json.JSONDecodeError:
            i += 1
    return out


def windows(v: dict) -> list[dict]:
    """대본 조각을 WINDOW 초 구간으로 묶는다. page = 구간 번호(1부터), t0 = 구간 시작 초.
    정책 표시를 붙여 색인한다: 가사 줄은 이어서 ♪ … ♪, 외국어 문장은 〔…〕, 효과 표시는 빼고, 자막 오류 의심 말엔 [?→추정].
    줄 단위 parts 를 함께 남겨 읽기 화면이 분류별로 그린다."""
    ks = policy.kinds()
    out: dict[int, list[dict]] = {}
    for seg in v["segments"]:
        out.setdefault(int(seg["start"] // WINDOW), []).append(seg)
    via = f"transcript_{v['source']}"
    blocks = []
    for w, segs in sorted(out.items()):
        text, run = [], []
        for sg in segs + [{"kind": "end", "text": ""}]:
            k = sg.get("kind", "speech")
            if k == "lyrics":
                run.append(policy.mark_line(sg))
                continue
            if run:
                a, b = ks["lyrics"]["mark"]
                text.append(a + " / ".join(run) + b)
                run = []
            if k == "foreign":
                a, b = ks["foreign"]["mark"]
                text.append(a + policy.mark_line(sg) + b)
            elif k not in ("end", "cue"):
                text.append(policy.mark_line(sg))
        blocks.append({"page": w + 1, "t0": w * WINDOW, "text": " ".join(t for t in text if t), "via": via,
                       "parts": [{"s": round(sg["start"], 2), "k": sg.get("kind", "speech"), "t": sg["text"],
                                  "proper": sg.get("proper", []), "garbled": sg.get("garbled", [])} for sg in segs]})
    return blocks


def reindex(sid: str, log=print, relabel: bool = False) -> dict:
    """영상 문서를 원문(.video.json)에서 다시 색인 — 줄 분류를 손으로 고쳤을 때(relabel=False, 고친 줄만 반영)
    또는 정책이 바뀌었을 때(relabel=True, 정책대로 전체 다시 분류). 영상을 다시 받지는 않는다."""
    raw = RAW / f"{sid}.video.json"
    if not raw.exists():
        raise ValueError("영상 문서가 아니에요")
    v = json.loads(raw.read_text(encoding="utf-8"))
    if relabel or v.get("policy_version") != policy.load()["version"]:
        label_transcript(v, log)
    for sg in v["segments"]:
        sg.setdefault("auto_kind", sg["kind"])  # auto_kind 이전에 만든 문서: override 가 없으면 지금 kind 가 자동값
        k = v.get("overrides", {}).get(f"{sg['start']:.2f}")
        sg["kind"] = k if k in policy.kinds() else sg["auto_kind"]
    raw.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    for c in ("vlm_base", "vlm_ctx", "rapidocr_base", "rapidocr_ctx"):
        client().delete(c, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="doc", match=models.MatchValue(value=sid))])))
    for name in ENGINES:
        p = ROOT / "data" / "context" / f"{name}.jsonl"
        p.write_text("".join(l for l in p.read_text(encoding="utf-8").splitlines(True)
                             if not json.loads(l)["id"].startswith(sid + ":")), encoding="utf-8")
    title = next(s["title"] for s in json.loads((ROOT / "sources.json").read_text()) if s["id"] == sid)
    return _index(sid, raw, title, log)


def set_kind(sid: str, starts: list[float], kind: str) -> None:
    """줄 단위로 가사/말 표시를 고친다(kind=auto 면 자동 판별로 되돌림)."""
    raw = RAW / f"{sid}.video.json"
    v = json.loads(raw.read_text(encoding="utf-8"))
    ov = v.setdefault("overrides", {})
    for st in starts:
        key = f"{float(st):.2f}"
        if kind == "auto":
            ov.pop(key, None)
        else:
            ov[key] = kind
    raw.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


def ingest_video(url: str, title: str = "", log=print) -> dict:
    """영상 주소 → 대본. 사람이 단 자막·자동 자막(ko→en) 우선, 없으면 오디오를 받아 faster-whisper 로 받아쓴다."""
    import tempfile
    import yt_dlp
    from aiocr.guard import check_host
    check_host(url)
    log("수집", "영상 정보를 가져오는 중")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        info = y.extract_info(url, download=False)
    dur = int(info.get("duration") or 0)
    if dur > 3 * 3600:
        raise ValueError("3시간이 넘는 영상은 아직 받지 않아요")
    segs, source = [], ""
    for kind in ("subtitles", "automatic_captions"):
        tracks = info.get(kind) or {}
        lang = next((l for l in ("ko", "ko-KR", "en", "en-US") if l in tracks), None)
        vtt = next((t for t in tracks.get(lang, []) if t.get("ext") == "vtt"), None) if lang else None
        if vtt:
            try:
                segs = _vtt(safe_fetch(vtt["url"]).decode("utf-8", "ignore"))
                source = "caption" if kind == "subtitles" else "autocaption"
                log("수집", f"자막 사용 ({lang}, {'사람' if source == 'caption' else '자동'}) · {len(segs)}줄")
                break
            except Exception as e:  # 자막 요청이 막히면(이 IP 에서 실측) 받아쓰기로
                log("수집", f"자막을 받지 못해 받아쓰기로 전환 ({type(e).__name__})")
    if not segs:
        from faster_whisper import WhisperModel
        with tempfile.TemporaryDirectory() as tmp:
            log("수집", f"오디오 받는 중 ({dur // 60}분 {dur % 60}초)")
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": "bestaudio/best",
                                   "outtmpl": f"{tmp}/a.%(ext)s"}) as y:
                y.download([url])
            audio = next(Path(tmp).glob("a.*"))
            log("파싱", "받아쓰기 준비 중")
            model = WhisperModel(STT_MODEL, device="cpu", compute_type="int8", local_files_only=True)
            # faster-whisper 의 PyAV 디코더가 설치된 av 와 맞지 않아(metadata_errors 인자, 11-10 실측) ffmpeg 로 직접 16kHz 모노 디코딩
            import subprocess
            import numpy as np
            pcm = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "16000",
                                  "-f", "f32le", "-"], capture_output=True, check=True).stdout
            it, meta = model.transcribe(np.frombuffer(pcm, np.float32), vad_filter=True, beam_size=1)
            for sg in it:
                if sg.text.strip() and not HALLUCINATION.match(sg.text.strip()):
                    segs.append({"start": sg.start, "text": sg.text.strip()})
                log("파싱", f"받아쓰기 {int(sg.end) // 60}:{int(sg.end) % 60:02d} / {dur // 60}:{dur % 60:02d}")
            source = "stt"
            log("파싱", f"받아쓰기 완료 · 언어 {meta.language} · {len(segs)}구간")
    if not segs:
        raise ValueError("영상에서 말소리를 찾지 못했어요")
    vid = {"url": url, "title": info.get("title") or url, "duration": dur, "uploader": info.get("uploader"),
           "source": source, "segments": segs}
    label_transcript(vid, log)
    return _ingest(json.dumps(vid, ensure_ascii=False).encode(), ".video.json", slug_hint=url,
                   origin={"kind": "video", "url": url, "duration": dur}, title=title or vid["title"], log=log)


def _ingest(data: bytes, ext: str, slug_hint: str, origin: dict, title: str, log) -> dict:
    src_f = ROOT / "sources.json"
    sources = json.loads(src_f.read_text())
    sid = slug(slug_hint, {s["id"] for s in sources})
    raw = RAW / f"{sid}{ext}"
    raw.write_bytes(data)
    title = title or title_of(raw, slug_hint)
    where = origin.get("url") or origin.get("path")
    sources.append({"sha256": hashlib.sha256(data).hexdigest(), "id": sid, **origin, "title": title, "group": "user"})
    src_f.write_text(json.dumps(sources, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with open(ROOT / "wiki" / "index.md", "a", encoding="utf-8") as f:
        f.write(f"| {sid} | user | {raw.name} | {title} | {where} |\n")
    with open(ROOT / "wiki" / "log.md", "a", encoding="utf-8") as f:
        f.write(f"- {dt.date.today()} ingest {sid} → raw/{raw.name} ({raw.stat().st_size:,} B) · 앱 추가\n")
    log("수집", f"{raw.name} {raw.stat().st_size:,} B · {title}")
    return _index(sid, raw, title, log)


def _index(sid: str, raw: Path, title: str, log) -> dict:
    """원문 → 엔진별 파싱 → 청크 → 맥락 → 색인 4개 upsert. 첫 수집과 reindex 가 같이 쓴다."""
    cache_f = ROOT / "data" / "context" / "cache.jsonl"
    cache = {r["key"]: r["context"] for r in map(json.loads, cache_f.open())} if cache_f.exists() else {}
    qc, emb, out = client(), embedder(), {"id": sid, "title": title, "chunks": {}}
    for engine, ocr in ENGINES.items():
        blocks = _parse(raw, ocr)
        if not any(b["text"].strip() for b in blocks):
            raise ValueError("본문을 추출하지 못했습니다 (빈 문서)")
        pdir = ROOT / "data" / "parsed" / engine
        with open(pdir / f"{sid}.jsonl", "w", encoding="utf-8") as w:
            for b in blocks:
                w.write(json.dumps({"doc": sid, **b}, ensure_ascii=False) + "\n")
        chunks = [{"id": f"{sid}:{b['page']}:{n}", "doc": sid, "page": b["page"], "text": c}
                  for b in blocks for n, c in enumerate(chunk_block(b["text"]))]
        log("파싱", f"{engine}: {len(blocks)}쪽 → 청크 {len(chunks)}개 ({', '.join(sorted({b['via'] for b in blocks}))})")

        doc = "\n".join(b["text"] for b in blocks)[:DOC_CAP]
        ctx = {}
        with cache_f.open("a", encoding="utf-8") as cw:
            for i, c in enumerate(chunks):
                key = hashlib.sha256((doc[:2000] + "\x00" + c["text"]).encode()).hexdigest()[:24]
                if key not in cache:
                    cache[key] = gen(DOC_T.format(doc=doc) + CHUNK_T.format(chunk=c["text"]))
                    cw.write(json.dumps({"key": key, "id": c["id"], "context": cache[key]}, ensure_ascii=False) + "\n")
                ctx[c["id"]] = cache[key]
                log("맥락", f"{engine}: {i + 1}/{len(chunks)}")
        with open(ROOT / "data" / "context" / f"{engine}.jsonl", "a", encoding="utf-8") as f:
            f.writelines(json.dumps({"id": k, "context": v}, ensure_ascii=False) + "\n" for k, v in ctx.items())

        for variant in ("base", "ctx"):
            name = f"{engine}_{variant}"
            texts = [(ctx[c["id"]] + "\n\n" + c["text"]) if variant == "ctx" else c["text"] for c in chunks]
            dense, sparse = emb.encode(texts)
            qc.upsert(name, [models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}/{c['id']}")),
                vector={"dense": dense[i].tolist(), "sparse": models.SparseVector(indices=list(sparse[i]), values=list(sparse[i].values()))},
                payload={**c, "context": ctx[c["id"]] if variant == "ctx" else ""}) for i, c in enumerate(chunks)])
            log("색인", f"{name} +{len(chunks)}")
        out["chunks"][engine] = len(chunks)
    log("완료", f"{sid} · 청크 " + " · ".join(f"{k} {v}" for k, v in out["chunks"].items()))
    return out


def remove(sid: str) -> None:
    """앱에서 추가한 문서(group=user)만 모든 저장소에서 뺀다 — 평가 말뭉치 18개는 지우지 않는다."""
    src_f = ROOT / "sources.json"
    src = json.loads(src_f.read_text())
    doc = next((s for s in src if s["id"] == sid), None)
    if not doc:
        raise ValueError(f"없는 문서: {sid}")
    if doc["group"] != "user":
        raise ValueError("평가에 쓰는 기본 문서는 지울 수 없어요")
    for c in ("vlm_base", "vlm_ctx", "rapidocr_base", "rapidocr_ctx"):
        client().delete(c, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="doc", match=models.MatchValue(value=sid))])))
    src_f.write_text(json.dumps([s for s in src if s["id"] != sid], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    idx = ROOT / "wiki" / "index.md"
    idx.write_text("".join(l for l in idx.read_text(encoding="utf-8").splitlines(True) if not l.startswith(f"| {sid} |")), encoding="utf-8")
    with open(ROOT / "wiki" / "log.md", "a", encoding="utf-8") as f:
        f.write(f"- {dt.date.today()} remove {sid} · 앱 삭제\n")
    for name in ("cache", *ENGINES):
        p = ROOT / "data" / "context" / f"{name}.jsonl"
        p.write_text("".join(l for l in p.read_text(encoding="utf-8").splitlines(True)
                             if not json.loads(l)["id"].startswith(sid + ":")), encoding="utf-8")
    for p in [*RAW.glob(f"{sid}.*"), *(ROOT / "data" / "parsed").glob(f"*/{sid}.jsonl")]:
        p.unlink()


if __name__ == "__main__":
    print(ingest(sys.argv[1], *sys.argv[2:], log=lambda s, m: print(f"[{s}] {m}", flush=True)))
