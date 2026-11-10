"""보고서 모드 — 문서 1개 또는 2~4개 비교. 통계 절(scikit-learn·kiwi)은 몇 초, LLM 절은 차례로 채운다.

절: 요약 · 핵심 키워드(c-TF-IDF) · 대비 키워드(로그오즈 비율 + 사전, Monroe 2008 / 문서 안 대립 개념은 LLM) · topic(KMeans,
실루엣으로 k 자동) · 벡터 지도(PCA 50 → t-SNE 2D, 말뭉치 상태별 캐시) · 색인 통계 · 문서 구조 · 수치 모음 · 용어집 · 읽기 질문.
저장: data/reports/<문서들>.json — 청크 지문이 바뀌면 '오래됨'. 사용: python -m aiocr.docreport <doc> [doc ...]
"""
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
from qdrant_client import models

from aiocr import guard, policy
from aiocr.answer import ask_claude
from aiocr.pipeline import _ollama, spread
from aiocr.rag import CHUNK, OVERLAP, client, load_chunks

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "reports"
COL = "vlm_base"  # 지도·topic 은 맥락을 붙이지 않은 원문 임베딩으로
KO_STOP = {"이것", "그것", "경우", "정도", "사용", "부분", "때문", "가지", "대한", "통해", "위해", "관련", "다음", "이번", "하나"}
NUM_UNIT = re.compile(r"\d[\d,.]*\s*(%|퍼센트|배|ms|초|분|시간|개|건|명|쪽|원|달러|만|억|[KMB]\b|x\b|USD|MB|GB)", re.I)


# ── 토큰화 ───────────────────────────────────────────────
@lru_cache
def kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


@lru_cache
def en_stop() -> frozenset:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
    return frozenset(ENGLISH_STOP_WORDS | {"et", "al", "fig", "table", "use", "used", "using", "based", "paper", "method", "methods", "results",
                                                 "arxiv", "ieee", "conference", "proceeding", "preprint", "corr", "abs", "vol", "pp", "journal", "acm", "cvpr", "iccv", "eccv",
                                                 "nip", "icdar", "workshop", "international", "http", "https", "www", "com", "org", "doi"})


@lru_cache(maxsize=4096)
def tokens(text: str) -> tuple[str, ...]:
    """명사(NNG·NNP)와 외국어(SL)만. 영어는 소문자·불용어 제거, 한 글자는 버린다."""
    out = []
    for w in kiwi().tokenize(text):
        if w.tag == "SL":
            t = re.sub(r"[^a-z0-9+\-]", "", w.form.lower())
            if len(t) > 4 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
                t = t[:-1]  # 가벼운 단수화 — sequences → sequence
            if len(t) > 2 and t not in en_stop():
                out.append(t)
        elif w.tag in ("NNG", "NNP") and len(w.form) > 1 and w.form not in KO_STOP:
            out.append(w.form)
    return tuple(out)


def corpus() -> list[dict]:
    return [{**c, "tok": tokens(c["text"])} for c in load_chunks("vlm")]


def fingerprint(chunks: list[dict]) -> str:
    return hashlib.sha256("".join(c["id"] + hashlib.md5(c["text"].encode()).hexdigest() for c in chunks).encode()).hexdigest()[:16]


# ── 키워드 ───────────────────────────────────────────────
def ctfidf(target: list[dict], all_chunks: list[dict], k: int = 15) -> list[dict]:
    """문서(들) 묶음 대 말뭉치 문서 단위 c-TF-IDF. 나오는 쪽·청크도 함께."""
    docs = {}
    for c in all_chunks:
        docs.setdefault(c["doc"], Counter()).update(c["tok"])
    df = Counter(t for cnt in docs.values() for t in cnt)
    tf = Counter(t for c in target for t in c["tok"])
    total = sum(tf.values()) or 1
    n = len(docs)
    scored = sorted(((tf[t] / total) * math.log(1 + n / df[t]), t) for t in tf if tf[t] >= 2)[::-1][:k]
    return [{"term": t, "score": round(s, 4), "count": tf[t], **where(t, target)} for s, t in scored]


def where(term: str, chunks: list[dict]) -> dict:
    hit = [c for c in chunks if term in c["tok"]]
    return {"pages": sorted({c["page"] for c in hit})[:12], "chunk": hit[0]["id"] if hit else None}


def log_odds(a: list[dict], b: list[dict], prior: list[dict], k: int = 10) -> list[dict]:
    """a 가 b 보다 자주 쓰는 말 — 사전(말뭉치 빈도)을 넣은 로그오즈 비율의 z 점수(Monroe et al. 2008)."""
    ya, yb, pr = Counter(t for c in a for t in c["tok"]), Counter(t for c in b for t in c["tok"]), Counter(t for c in prior for t in c["tok"])
    na, nb, a0 = sum(ya.values()), sum(yb.values()), sum(pr.values())
    scale = 500 / max(a0, 1)  # 사전 세기 — 말뭉치 크기와 무관하게 약 500 토큰 분량
    out = []
    for t in ya:
        if ya[t] < 2:
            continue
        al = pr[t] * scale + 0.01
        al0 = a0 * scale
        d = math.log((ya[t] + al) / (na + al0 - ya[t] - al)) - math.log((yb[t] + al) / (nb + al0 - yb[t] - al))
        z = d / math.sqrt(1 / (ya[t] + al) + 1 / (yb[t] + al))
        out.append((z, t))
    return [{"term": t, "z": round(z, 2), "count": ya[t], **where(t, a)} for z, t in sorted(out)[::-1][:k]]


# ── 벡터 ─────────────────────────────────────────────────
def vectors(ids: set[str] | None = None) -> dict[str, np.ndarray]:
    pts, nxt, out = None, None, {}
    while True:
        pts, nxt = client().scroll(COL, limit=512, offset=nxt, with_payload=["id", "doc"], with_vectors=["dense"])
        for p in pts:
            if ids is None or p.payload["id"] in ids:
                out[p.payload["id"]] = np.asarray(p.vector["dense"], dtype=np.float32)
        if nxt is None:
            return out


def sparse_nnz(ids: list[str]) -> float:
    pts, _ = client().scroll(COL, scroll_filter=models.Filter(must=[models.FieldCondition(key="id", match=models.MatchAny(any=ids[:256]))]),
                             limit=256, with_vectors=["sparse"])
    return round(float(np.mean([len(p.vector["sparse"].indices) for p in pts])), 1) if pts else 0.0


def vector_map(all_chunks: list[dict]) -> dict[str, list[float]]:
    """말뭉치 전체 청크 2D 좌표(0~1). PCA 50 → t-SNE. 말뭉치 지문별로 캐시."""
    fp = fingerprint(all_chunks)
    f = OUT / f"_map-{fp}.json"
    if f.exists():
        return json.loads(f.read_text())
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE
    vs = vectors()
    ids = [c["id"] for c in all_chunks if c["id"] in vs]
    X = np.stack([vs[i] for i in ids])
    X = PCA(n_components=min(50, len(ids) - 1), random_state=0).fit_transform(X)
    Y = TSNE(n_components=2, perplexity=min(30, max(5, len(ids) // 20)), init="pca", random_state=0).fit_transform(X)
    Y = (Y - Y.min(0)) / (Y.max(0) - Y.min(0) + 1e-9)
    out = {i: [round(float(x), 4), round(float(y), 4)] for i, (x, y) in zip(ids, Y)}
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("_map-*.json"):
        old.unlink()
    f.write_text(json.dumps(out))
    return out


_NN: dict = {}


@lru_cache
def tok():
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(ROOT / "models" / "bge-m3-onnx" / "tokenizer.json"))


def word_weights(text: str, sp: dict[int, float]) -> dict[str, float]:
    """sparse 가중치는 서브워드 토큰(예: 'probabil'·'scription') 단위라, 원문에서 그 토큰이 속한 낱말로 되돌려 합친다."""
    text = re.sub(r"\[[^\]\n]{1,8}\]", lambda m: " " * len(m.group()), text)  # [음악]·[웃음] 같은 효과 표시는 내용이 아니다(자리 길이는 유지)
    enc = tok().encode(text[:4000])
    spans = [(m.start(), m.end()) for m in re.finditer(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣+\-]*", text[:4000])]
    out: dict[str, float] = {}
    j = 0
    for tid, (st, en) in zip(enc.ids, enc.offsets):
        w = sp.get(tid)
        if not w or st == en:
            continue
        while j < len(spans) and spans[j][1] <= st:
            j += 1
        if j < len(spans) and spans[j][0] <= st < spans[j][1]:
            word = text[spans[j][0]:spans[j][1]]
            if word.isascii():
                key = word.lower()
            else:  # 한국어는 조사·어미를 떼고 명사만(랩을·랩으로 → 랩)
                key = "".join(t for t in tokens(word) if not t.isascii()) or ""
                if not key:
                    continue
            out[key] = max(out.get(key, 0.0), float(w))
    return out


def sparse_terms(ww: dict[str, float], k: int = 12) -> list[dict]:
    """낱말 가중치 상위 — 한 글자·숫자뿐·불용어는 뺀다."""
    out = []
    for t, w in sorted(ww.items(), key=lambda x: -x[1]):
        if (len(t) < 2 and not t.isascii()) or len(t) < 2 or t.isdigit() or t.lower() in en_stop() or t in KO_STOP:
            continue
        out.append({"term": t, "w": round(w, 3)})
        if len(out) >= k:
            break
    return out


def _sparse_all() -> dict[str, dict[int, float]]:
    out, nxt = {}, None
    while True:
        pts, nxt = client().scroll(COL, limit=512, offset=nxt, with_payload=["id"], with_vectors=["sparse"])
        for p in pts:
            out[p.payload["id"]] = dict(zip(p.vector["sparse"].indices, p.vector["sparse"].values))
        if nxt is None:
            return out


def neighbors(cid: str, k: int = 8) -> dict:
    """원래 1024차원 공간의 코사인 최근접 청크 — 2D 지도(t-SNE)의 거리는 왜곡될 수 있어 연관성은 여기서 잰다."""
    n = client().count(COL).count
    if _NN.get("n") != n:  # 문서가 늘거나 줄면 다시 읽는다
        vs = vectors()
        meta = {c["id"]: c for c in load_chunks("vlm")}
        ids = [i for i in vs if i in meta]
        M = np.stack([vs[i] for i in ids])
        M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
        titles = {s["id"]: s["title"] for s in json.loads((ROOT / "sources.json").read_text())}
        _NN.update(n=n, ids=ids, pos={i: j for j, i in enumerate(ids)}, M=M, meta=meta, titles=titles, sp=_sparse_all())
    if cid not in _NN["pos"]:
        return {"terms": [], "items": []}
    me = word_weights(_NN["meta"][cid]["text"], _NN["sp"].get(cid, {}))
    sims = _NN["M"] @ _NN["M"][_NN["pos"][cid]]
    order = np.argsort(-sims)[1:k + 1]
    out = []
    for j in order:
        c = _NN["meta"][_NN["ids"][j]]
        other = word_weights(c["text"], _NN["sp"].get(c["id"], {}))
        shared = {t: min(me[t], other[t]) for t in me.keys() & other.keys()}  # 두 청크가 함께 무게를 둔 낱말
        out.append({"id": c["id"], "doc": c["doc"], "title": _NN["titles"].get(c["doc"], c["doc"]), "page": c["page"],
                    "sim": round(float(sims[j]), 3), "text": c["text"][:180], "shared": [x["term"] for x in sparse_terms(shared, 5)]})
    return {"terms": sparse_terms(me), "items": out}


def topics(target: list[dict], k: int | None = None) -> dict:
    """대상 청크 임베딩 KMeans. k 를 안 주면 실루엣 점수로 2~8 중 고른다."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    vs = vectors({c["id"] for c in target})
    cs = [c for c in target if c["id"] in vs]
    X = np.stack([vs[c["id"]] for c in cs])
    n = len(cs)
    if n >= 12:  # 1024 차원에서는 실루엣이 모두 0.1 근처로 비슷해 k 를 못 가른다 → PCA 10 차원에서 군집
        from sklearn.decomposition import PCA
        X = PCA(n_components=10, random_state=0).fit_transform(X)
    if n < 4:
        labels, k, sil = np.zeros(n, int), 1, None
    else:
        cand = range(2, max(2, min(8, n // 4)) + 1)  # 청크 4개당 topic 1개를 넘지 않게
        fits = {kk: KMeans(n_clusters=kk, n_init=10, random_state=0).fit(X) for kk in ([k] if k else cand)}
        sils = {kk: float(silhouette_score(X, m.labels_)) for kk, m in fits.items()}
        best = max(sils.values())
        k = k or min(kk for kk, v in sils.items() if v >= best * 0.9)  # 점수가 비슷하면 적은 쪽(읽기 쉬움)
        labels, sil = fits[k].labels_, round(sils[k], 3)
    out = []
    for t in range(k):
        mem = [c for c, l in zip(cs, labels) if l == t]
        cen = X[labels == t].mean(0)
        rep = mem[int(np.argmin([float(np.linalg.norm(X[i] - cen)) for i in np.where(labels == t)[0]]))]
        out.append({"topic": t, "size": len(mem), "keywords": [x["term"] for x in ctfidf(mem, target, 6)],
                    "pages": sorted({c["page"] for c in mem}), "rep": rep["id"], "rep_text": rep["text"][:400],
                    "docs": dict(Counter(c["doc"] for c in mem))})
    return {"k": k, "silhouette": sil, "assign": {c["id"]: int(l) for c, l in zip(cs, labels)}, "topics": out}


# ── 구조·수치 후보 ───────────────────────────────────────
HEAD = re.compile(r"^(#{1,3}\s+.+|(?:[IVX]+|\d+(?:\.\d+)*)\.?\s+[A-Z가-힣][^\n]{2,70}|■\s*.+)$")


def headings(doc: str, target: list[dict]) -> list[dict]:
    """목차 — 청크는 제목 줄이 문단과 붙어 있어 원래 쪽 텍스트(data/parsed)에서 찾는다."""
    f = ROOT / "data" / "parsed" / "vlm" / f"{doc}.jsonl"
    seen, out = set(), []
    for b in (map(json.loads, f.open(encoding="utf-8")) if f.exists() else []):
        for line in b["text"].splitlines():
            s = line.strip()
            if HEAD.match(s) and len(s) < 80 and s not in seen:
                seen.add(s)
                title = s.lstrip("#■ ").strip()
                ch = next((c for c in target if c["page"] == b["page"] and title[:30] in c["text"]), None) or \
                    next((c for c in target if c["page"] == b["page"]), None)
                out.append({"title": title, "page": b["page"], "chunk": ch["id"] if ch else None})
    return out[:24]


def number_lines(target: list[dict], k: int = 12) -> list[dict]:
    out, seen = [], set()
    for c in target:
        for sent in re.split(r"(?<=[.!?。])\s+|\n", c["text"]):
            s = sent.strip()
            if 20 < len(s) < 260 and NUM_UNIT.search(s) and s not in seen:
                seen.add(s)
                out.append({"text": s, "page": c["page"], "chunk": c["id"]})
    step = max(1, len(out) / k)
    return [out[int(i * step)] for i in range(min(k, len(out)))]


# ── LLM ──────────────────────────────────────────────────
def llm(prompt: str, who: str, n: int = 700) -> str:
    sys_ = "너는 문서 분석 보고서를 쓰는 사람이다. 주어진 근거만 쓰고, 한국어로, 요청한 JSON 만 출력한다." + policy.rules()  # 대본 정책(SSOT)
    return ask_claude(prompt, system=sys_)["text"] if who == "claude" else _ollama(prompt, system=sys_, num_predict=n)["text"]


def first_json(text: str, ok=lambda v: True):
    """처음으로 온전한 JSON 값 하나 — 7B 모델은 올바른 JSON 뒤에 설명·반복을 덧붙이곤 한다(11-10 실측)."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                v = dec.raw_decode(text[i:])[0]
            except json.JSONDecodeError:
                continue
            if ok(v):  # 앞에 '근거 [5]' 같은 글이 먼저 와도 기대한 모양의 JSON 만 받는다
                return v
    return None


def shape(kind: str):
    if kind == "objs":
        return lambda v: isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
    if kind == "strs":
        return lambda v: isinstance(v, list) and v and all(isinstance(x, str) for x in v)
    return lambda v: isinstance(v, dict)


def salvage(text: str) -> list[dict]:
    """배열이 중간에 깨졌을 때(모델이 셋째 항목에서 다른 언어로 빠지는 등) 온전한 객체만 건진다."""
    dec, out, i = json.JSONDecoder(), [], 0
    while (i := text.find("{", i)) >= 0:
        try:
            v, end = dec.raw_decode(text[i:])
            if isinstance(v, dict):
                out.append(v)
            i += end
        except json.JSONDecodeError:
            i += 1
    return out


HAN = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")  # 한자 + 가나


def has_han(v) -> bool:
    if isinstance(v, str):
        return bool(HAN.search(v))
    if isinstance(v, dict):
        return any(has_han(x) for x in v.values())
    if isinstance(v, list):
        return any(has_han(x) for x in v)
    return False


def llm_json(prompt: str, who: str, n: int = 700, kind: str = "objs"):
    """기대한 모양의 JSON 을 받고, 한자가 섞인 항목은 버린다(언어 가드). 남는 게 없으면 '한국어로만' 붙여 한 번 더."""
    for attempt in range(2):
        text = llm(prompt + ("\n\n반드시 한국어(한글)와 원문 영문 고유명만 써라. 한자·중국어·일본어(가나) 금지." if attempt else ""), who, n)
        v = first_json(text, shape(kind)) or (salvage(text) if kind == "objs" else None)
        if isinstance(v, list):
            v = [x for x in v if not has_han(x)]
        elif has_han(v):
            v = None
        if v:
            return v
    return None


def uniq(xs, key) -> list[dict]:
    seen, out = set(), []
    for x in xs if isinstance(xs, list) else []:
        if isinstance(x, dict) and key(x) not in seen:
            seen.add(key(x))
            out.append(x)
    return out


def ctx(chunks: list[dict], cap: int = 650) -> str:
    return "\n\n".join(f"[{i + 1}] ({c['page']}쪽) {c['text'][:cap]}" for i, c in enumerate(chunks))


# ── 조립 ─────────────────────────────────────────────────
def key_of(docs: list[str]) -> str:
    return "+".join(sorted(docs))


def saved(docs: list[str]) -> dict | None:
    f = OUT / f"{key_of(docs)}.json"
    if not f.exists():
        return None
    r = json.loads(f.read_text())
    target = [c for c in load_chunks("vlm") if c["doc"] in docs]
    r["stale"] = r.get("fingerprint") != fingerprint(target) or "chunks" not in r  # 옛 형식 저장본도 다시 만들도록
    return r


def build(docs: list[str], who: str = "local", emit=lambda r: None) -> dict:
    t0 = time.time()
    all_chunks = corpus()
    meta = {s["id"]: s for s in json.loads((ROOT / "sources.json").read_text())}
    target = [c for c in all_chunks if c["doc"] in docs]
    if not target:
        raise ValueError("청크가 없는 문서예요")
    rest = [c for c in all_chunks if c["doc"] not in docs]
    compare = len(docs) > 1
    r = {"docs": docs, "titles": {d: meta.get(d, {}).get("title", d) for d in docs}, "compare": compare, "llm": who,
         "fingerprint": fingerprint(target), "made": time.time(), "done": [], "sections": {},
         "chunks": {c["id"]: {"text": c["text"], "page": c["page"], "doc": c["doc"]} for c in target}}  # 보고서 → 원문 이동·강조용
    S = r["sections"]

    def put(name, val):
        S[name] = val
        if name not in r["done"]:
            r["done"].append(name)
        emit(r)

    # 통계 절 — 몇 초
    put("keywords", ctfidf(target, all_chunks))
    if compare:
        put("contrast", {d: log_odds([c for c in target if c["doc"] == d], [c for c in target if c["doc"] != d], all_chunks) for d in docs})
        kw = {d: {x["term"] for x in ctfidf([c for c in target if c["doc"] == d], all_chunks, 25)} for d in docs}
        put("shared", sorted(set.intersection(*kw.values())) if len(kw) > 1 else [])
    else:
        put("contrast", {docs[0]: log_odds(target, rest, all_chunks)})
    tp = topics(target)
    put("topics", tp)
    mp = vector_map(all_chunks)
    put("map", {"points": [{"id": c["id"], "doc": c["doc"], "page": c["page"], "xy": mp[c["id"]]} for c in all_chunks if c["id"] in mp]})
    pages = {d: sorted({c["page"] for c in target if c["doc"] == d}) for d in docs}
    put("stats", {"chunks": len(target), "corpus_chunks": len(all_chunks), "corpus_docs": len({c["doc"] for c in all_chunks}),
                  "pages": {d: len(p) for d, p in pages.items()}, "dense_dim": 1024, "sparse_nnz": sparse_nnz([c["id"] for c in target]),
                  "chunk_size": CHUNK, "overlap": OVERLAP, "avg_chunk_chars": round(sum(len(c["text"]) for c in target) / len(target)),
                  "tokens": sum(len(c["tok"]) for c in target), "vocab": len({t for c in target for t in c["tok"]}),
                  "embedding": "bge-m3 (ONNX) dense 1024 + learned sparse", "collections": ["vlm_base", "vlm_ctx", "rapidocr_base", "rapidocr_ctx"],
                  "map_method": "PCA 50 → t-SNE 2D (scikit-learn)", "topic_method": f"KMeans k={tp['k']} · 실루엣 {tp['silhouette']}"})
    heads = headings(docs[0], target) if not compare else []
    nums = number_lines(target)

    # LLM 절 — 끝나는 대로 채운다
    sp = spread(COL, docs, 10 if not compare else 4 * len(docs))
    if compare:
        rows = []
        for d in docs:
            sp_d = [c for c in sp if c["doc"] == d] or spread(COL, [d], 4)
            j = llm_json(f"[근거]\n{ctx(sp_d, 500)}\n\n위 문서를 JSON 으로 요약하라: "
                         '{"gist": "<한 문장>", "points": ["<핵심 1>", "<핵심 2>", "<핵심 3>"]}', who, kind="obj")
            rows.append({"doc": d, **(j if isinstance(j, dict) else {"gist": "요약을 만들지 못했어요", "points": []})})
        put("summary", {"rows": rows})
    else:
        j = llm_json(f"[근거]\n{ctx(sp)}\n\n위 근거로 문서 전체를 요약하라. JSON: "
                     '{"gist": "<한 문장 요지>", "points": ["<핵심 항목, 끝에 근거 번호 [n]>", ...3~6개]}', who, kind="obj")
        j = j if isinstance(j, dict) else {"gist": "요약을 만들지 못했어요", "points": []}
        # 7B 가 points 에 숫자(근거 번호)를 따로 섞어 돌려주기도 한다(11-11 영상 보고서 실측) — 글자만 남긴다
        j = {**j, "gist": str(j.get("gist") or ""), "points": [p for p in (j.get("points") or []) if isinstance(p, str)]}
        issues = guard.check_answer(" ".join([j.get("gist", "")] + j.get("points", [])), sp)
        put("summary", {**j, "cites": [{"n": i + 1, "id": c["id"], "page": c["page"]} for i, c in enumerate(sp)],
                        "issues": {k: v for k, v in issues.items() if k != "cites"}})

    j = llm_json(f"[근거]\n{ctx(sp, 500)}\n\n" + ("문서들 사이에서 서로 다른 관점·접근으로 맞서는 개념 쌍" if compare else "문서 안에서 서로 비교·대비되는 개념 쌍")
                 + "을 3~5개 JSON 배열로: " '[{"a": "<개념>", "b": "<맞서는 개념>", "diff": "<차이 한 문장>", "cite": <근거 번호>}]', who)
    put("pairs", [{**p, "page": sp[p["cite"] - 1]["page"] if isinstance(p.get("cite"), int) and 0 < p["cite"] <= len(sp) else None,
                   "chunk": sp[p["cite"] - 1]["id"] if isinstance(p.get("cite"), int) and 0 < p["cite"] <= len(sp) else None}
                  for p in uniq(j, lambda x: (x.get("a"), x.get("b"))) if p.get("a") and p.get("b")][:5])

    if tp["topics"]:
        desc = "\n".join(f"topic {t['topic']}: 키워드 {', '.join(t['keywords'])} / 대표 글 {t['rep_text'][:250]}" for t in tp["topics"])
        j = llm_json(f"{desc}\n\n각 topic 에 짧은 한국어 이름(2~6단어, 고유명은 원문 표기)과 한국어 한 문장 설명을 붙여 JSON 배열로: "
                     '[{"topic": <번호>, "name": "...", "desc": "..."}]', who)
        names = {x.get("topic"): x for x in (j if isinstance(j, list) else []) if isinstance(x, dict)}
        for t in tp["topics"]:
            t["name"] = names.get(t["topic"], {}).get("name") or " · ".join(t["keywords"][:3])
            t["desc"] = names.get(t["topic"], {}).get("desc", "")
        put("topics", tp)

    if not compare:
        if heads:
            j = llm_json("\n".join(f"{i + 1}. {h['title']} ({h['page']}쪽)" for i, h in enumerate(heads)) + f"\n\n[본문 일부]\n{ctx(sp, 300)}\n\n"
                         "위 목차의 각 항목이 무엇을 다루는지 한 줄씩 JSON 배열로(번호 순서 유지): " '["<한 줄>", ...]', who, 900, kind="strs")
            for h, line in zip(heads, j if isinstance(j, list) else []):
                h["line"] = line if isinstance(line, str) else ""
        else:  # 목차가 없으면 쪽(영상은 구간)마다 한 줄
            heads = [{"title": f"{p}쪽", "page": p, "chunk": next(c["id"] for c in target if c["page"] == p)} for p in pages[docs[0]][:12]]
            per = {p: next(c["text"] for c in target if c["page"] == p)[:300] for p in pages[docs[0]][:12]}
            j = llm_json("\n".join(f"{p}: {t}" for p, t in per.items()) + "\n\n각 번호가 다루는 내용을 한 줄씩 JSON 배열로: " '["<한 줄>", ...]', who, 900, kind="strs")
            for h, line in zip(heads, j if isinstance(j, list) else []):
                h["line"] = line if isinstance(line, str) else ""
        put("structure", heads)

        if nums:
            j = llm_json("\n".join(f"{i + 1}. ({n['page']}쪽) {n['text']}" for i, n in enumerate(nums)) +
                         "\n\n각 문장의 핵심 수치와 그 뜻을 JSON 배열로(문장에 있는 숫자만): " '[{"i": <번호>, "value": "<수치 그대로>", "meaning": "<뜻 한 줄>"}]', who, 900)
            rows = []
            for x in (j if isinstance(j, list) else []):
                if isinstance(x, dict) and isinstance(x.get("i"), int) and 0 < x["i"] <= len(nums):
                    n = nums[x["i"] - 1]
                    v = str(x.get("value", ""))
                    if v and re.sub(r"[\s,]", "", v)[:6] in re.sub(r"[\s,]", "", n["text"]) and v not in {r["value"] for r in rows}:  # 원문에 없는 수치·중복은 버린다
                        rows.append({**n, "value": v, "meaning": x.get("meaning", "")})
            put("numbers", rows)
        else:
            put("numbers", [])

        terms = [k["term"] for k in S["keywords"][:10]]
        j = llm_json(f"[근거]\n{ctx(sp, 450)}\n\n용어: {', '.join(terms)}\n\n위 용어 중 이 문서를 읽는 데 필요한 5~8개를 근거에 기대어 풀이하라. JSON 배열: "
                     '[{"term": "<용어>", "def": "<뜻 한 문장>", "cite": <근거 번호>}]', who, 900)
        put("glossary", [{**g, "page": sp[g["cite"] - 1]["page"] if isinstance(g.get("cite"), int) and 0 < g["cite"] <= len(sp) else None,
                          "chunk": sp[g["cite"] - 1]["id"] if isinstance(g.get("cite"), int) and 0 < g["cite"] <= len(sp) else None}
                         for g in uniq(j, lambda x: x.get("term")) if g.get("term")][:8])

        j = llm_json(f"요지: {S['summary'].get('gist', '')}\n키워드: {', '.join(terms)}\n\n이 문서를 이해하려면 물어볼 만한 질문 4개를 JSON 배열로: " '["...", ...]', who, 400, kind="strs")
        put("questions", [q for q in (j if isinstance(j, list) else []) if isinstance(q, str)][:5])

    r["sec"] = round(time.time() - t0, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key_of(docs)}.json").write_text(json.dumps(r, ensure_ascii=False))
    emit(r)
    return r


def retopic(docs: list[str], k: int) -> dict:
    """슬라이더 — topic 군집만 다시(이름은 키워드로, LLM 이름은 다시 만들기 때)."""
    target = [c for c in corpus() if c["doc"] in docs]
    tp = topics(target, k)
    for t in tp["topics"]:
        t["name"] = " · ".join(t["keywords"][:3])
        t["desc"] = ""
    return tp


if __name__ == "__main__":
    rep = build(sys.argv[1:], emit=lambda r: print("·", r["done"][-1] if r["done"] else "", flush=True))
    print(json.dumps({k: (v if k != "map" else f"{len(v['points'])} points") for k, v in rep["sections"].items()}, ensure_ascii=False)[:3000])
    print(rep["sec"], "s")
