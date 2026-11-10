"""영상 대본 정책(SSOT) — policy/transcript.json 을 읽어 판별·색인 표시·프롬프트 규칙을 만든다. 규칙을 바꾸려면 그 파일만 고친다."""
import json
import re
from functools import lru_cache
from pathlib import Path

F = Path(__file__).resolve().parents[2] / "policy" / "transcript.json"


@lru_cache
def load() -> dict:
    return json.loads(F.read_text(encoding="utf-8"))


def kinds() -> dict[str, dict]:
    p = load()
    return {k["id"]: k for k in p["line_kinds"] + p["span_kinds"]}


def cue_re() -> re.Pattern:
    return re.compile(kinds()["cue"]["pattern"])


def mark_line(seg: dict) -> str:
    """색인할 줄 텍스트 — 효과 표시는 빼고, 자막 오류 의심 말 뒤에 추정을 붙인다(원문은 그대로). 가사·외국어 감싸기는 windows 가 한다."""
    if seg.get("kind") == "cue":
        return ""
    t = seg["text"]
    for g in seg.get("garbled", []):
        if g.get("orig") and g["orig"] in t and g.get("guess"):
            t = t.replace(g["orig"], g["orig"] + kinds()["garbled"]["mark"].format(guess=g["guess"]), 1)
    return re.sub(r"\[[^\]\n]{1,8}\]", " ", t).strip()


def rules(present: set[str] | None = None) -> str:
    """프롬프트에 넣을 규칙. present 를 주면 그 분류만(근거에 있는 표시로 판단)."""
    ks = kinds()
    ids = [i for i in ("lyrics", "foreign", "proper", "garbled", "cue") if present is None or i in present]
    lines = []
    for i in ids:
        k = ks[i]
        m = k.get("mark")
        how = f" (근거 표시: {m[0].strip()}…{m[1].strip()})" if isinstance(m, list) and m[0] else (" (근거 표시: [?→추정])" if i == "garbled" else "")
        lines.append(f"- {k['label']}{how}: {k['rule']}")
    return "\n[대본 정책]\n" + "\n".join(lines) if lines else ""


def present_in(texts: list[str]) -> set[str]:
    """근거 텍스트에 어떤 정책 표시가 있나 — 해당 규칙만 프롬프트에 붙이려고."""
    j = "\n".join(texts)
    ks = kinds()
    out = {"proper"}  # 고유명 규칙은 대본이면 늘 붙인다
    for i in ("lyrics", "foreign"):
        if ks[i]["mark"][0].strip() and ks[i]["mark"][0].strip() in j:
            out.add(i)
    if "[?→" in j:
        out.add("garbled")
    return out
