"""가드 — 수집 URL 안전 검사(SSRF), 근거 점수 임계값, 답변 사후 검사(언어·인용·숫자).

임계값은 eval/questions(30)·vague(12)·unanswerable(12) 의 rerank top-1 점수로 정했다(2026-11-10 실측):
답 없는 질문 11/12 < 0.25(최대 0.546) · 구체적 질문 최소 0.5465 · 막연한 질문은 0.005 까지 내려가 점수만으로 못 가른다
→ 0.60 이상 '확신 높음', 그 아래는 영문 고유명 대조, 고유명 없는 질문은 0.30 미만 거절.
"""
import ipaddress
import re
import socket
import urllib.request
from urllib.parse import urlparse

REFUSE_BELOW, LOW_BELOW = 0.30, 0.60
MAX_BYTES = 30 * 1024 * 1024
UA = {"User-Agent": "aiocr-collector/0.1"}


def check_host(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("http(s) 주소만 받습니다")
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"내부·사설 주소는 받지 않습니다 ({u.hostname} → {ip})")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_host(newurl)  # 리다이렉트 목적지도 다시 검사
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_fetch(url: str) -> bytes:
    check_host(url)
    opener = urllib.request.build_opener(_SafeRedirect)
    with opener.open(urllib.request.Request(url, headers=UA), timeout=60) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{MAX_BYTES // 2**20}MB 를 넘는 문서는 받지 않습니다")
    return data


GENERIC_EN = {"ocr", "pdf", "api", "ai", "app", "model", "usd"}
_norm = lambda x: re.sub(r"[^0-9a-z가-힣]", "", x.lower())


def entities(q: str) -> list[str]:
    """질문 속 영문 고유명(DBNet·Florence-2·UDOP 등). 흔한 일반어는 뺀다."""
    toks = re.findall(r"[A-Za-z][A-Za-z0-9.+\-]*[A-Za-z0-9]|[A-Za-z]{2,}", q)
    return [t for t in toks if t.lower() not in GENERIC_EN and len(_norm(t)) >= 2]


def confidence(q: str, hits: list[dict]) -> str:
    """rerank 점수만으로는 '막연하지만 답 있는 질문'(DBNet 핵심은? 0.005)과 '답 없는 질문'(Florence-2? 0.005)이 겹친다.
    0.6 미만에서는 질문의 영문 고유명이 근거에 모두 나오는지로 가른다(없으면 거절, 다 나오면 확신 낮음)."""
    top = hits[0]["score"] if hits else 0.0
    if top >= LOW_BELOW:
        return "high"
    ents = entities(q)
    if ents:
        pool = _norm("\n".join(h["doc"] + h.get("title", "") + h.get("context", "") + h["text"] for h in hits))
        return "low" if all(_norm(e) in pool for e in ents) else "none"
    return "low" if top >= REFUSE_BELOW else "none"


NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|억)?")


def _forms(n: str, unit: str | None) -> set[str]:
    """'830'+'만' → 8300000 · 8.3 (million) 처럼 근거에 영어 단위로 적혔을 수 있는 표기들."""
    n = n.replace(",", "")
    if not unit:
        return {n}
    v = float(n) * (1e4 if unit == "만" else 1e8)
    fmt = lambda x: f"{x:g}"
    out = {n + unit, fmt(v), f"{fmt(v / 1e3)}k", fmt(v / 1e6), fmt(v / 1e9)}
    if v == 1e6:
        out.add("million")
    if v == 1e9:
        out.add("billion")
    return out


def check_answer(text: str, hits: list[dict]) -> dict:
    """문제 목록을 돌려준다. 빈 dict = 통과."""
    issues = {}
    han = re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", text)  # 한자 + 일본어 가나(7B 가 '맥도ナルド'처럼 섞는다, 11-11 실측)
    if han:
        issues["lang"] = "".join(han[:12])
    elif len(re.findall(r"[가-힣]", text)) < 5:
        issues["lang"] = "한국어 아님"
    cites = [int(n) for n in re.findall(r"\[(\d+)\]", text)]
    if bad := [n for n in cites if not 1 <= n <= len(hits)]:
        issues["cites"] = bad
    body = re.sub(r"\[\d+\]", "", text)
    pool = "\n".join(hits[n - 1]["text"] for n in set(cites) if 1 <= n <= len(hits)) or "\n".join(h["text"] for h in hits)
    pool = pool.replace(",", "").lower()
    # 두 자리 이상 숫자·소수만 본다(한 자리 수는 '3단계' 처럼 근거와 무관하게 흔하다)
    miss = [n + (u or "") for n, u in NUM.findall(body)
            if len(n.replace(",", "").replace(".", "")) >= 2 and not any(f in pool for f in _forms(n, u))]
    if miss:
        issues["numbers"] = miss
    return issues
