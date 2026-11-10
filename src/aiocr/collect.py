"""수집 — sources.json 의 원문을 wiki/raw/ 에 그대로 보관하고 wiki/index.md·log.md 를 갱신한다(llm-wiki 패턴).

개인 문서 일부는 파서 경로 검증을 위해 형식을 바꿔 보관한다:
  as=pptx → 마크다운 절마다 슬라이드 1장인 .pptx
  as=scan → 텍스트 레이어 없는 이미지 PDF(약한 회전·잡음) + 정답 텍스트(.gt.txt, OCR CER 계산용)
"""
import datetime as dt
import json
import random
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "wiki" / "raw"
UA = {"User-Agent": "aiocr-collector/0.1"}
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def strip_front_matter(text: str) -> str:
    """YAML 앞머리를 읽는 글로 풀어 본문 앞에 붙인다(스캔·슬라이드에 담을 내용 — 이미지 경로·다이어그램 좌표는 뺀다)."""
    m = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.S)
    body = text[m.end():] if m else text
    head, skip = [], False
    for ln in (m.group(1).splitlines() if m else []):
        key = re.match(r"^(\w+):\s*(.*)$", ln)
        if key:
            skip = key.group(1) in ("images", "diagram", "repo", "status")
            if not skip:
                head.append(f"## {key.group(1)}" if not key.group(2) else f"{key.group(1)}: {key.group(2)}")
            continue
        if skip:
            continue
        item = re.sub(r"[{}\[\]\"]", "", ln.strip().lstrip("- ")).strip()
        item = re.sub(r"\b(name|desc|tech|done): ", "", item)
        if item:
            head.append(f"- {item}")
    return ("\n".join(head) + "\n\n" + body).replace("<!-- details -->", "").strip()


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def sections(md: str) -> list[tuple[str, list[str]]]:
    out, title, body = [], "개요", []
    for line in md.splitlines():
        if line.startswith("#"):
            if body:
                out.append((title, body))
            title, body = line.lstrip("# ").strip(), []
        elif line.strip() and not line.startswith("```") and not re.fullmatch(r"\|[-| :]+\|", line.strip()):
            body.append(line.strip().lstrip("- ").replace("**", ""))
    if body:
        out.append((title, body))
    return out


def to_pptx(md: str, title: str, dst: Path) -> None:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = title
    for head, lines in sections(md):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = head
        tf = s.placeholders[1].text_frame
        tf.text = lines[0]
        for ln in lines[1:]:
            tf.add_paragraph().text = ln
        for p in tf.paragraphs:
            for r in p.runs:
                r.font.size = Pt(12)
    prs.save(dst)


def to_scan(md: str, dst: Path, seed: int = 7) -> None:
    """A4 150dpi 페이지에 글자를 그리고 약하게 회전·잡음을 넣어 이미지 전용 PDF 로 저장."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rnd = random.Random(seed)
    font = ImageFont.truetype(FONT, 22)
    lines = []
    for head, body in sections(md):
        lines += [f"■ {head}"] + body + [""]
    wrapped = []
    for ln in lines:  # 페이지 폭(약 46자)에 맞춰 줄바꿈
        while len(ln) > 46:
            wrapped.append(ln[:46])
            ln = ln[46:]
        wrapped.append(ln)
    pages, per = [], 40
    for i in range(0, len(wrapped), per):
        img = Image.new("L", (1240, 1754), 255)
        d = ImageDraw.Draw(img)
        for j, ln in enumerate(wrapped[i:i + per]):
            d.text((90, 90 + j * 40), ln, font=font, fill=rnd.randint(0, 40))
        img = img.rotate(rnd.uniform(-1.2, 1.2), fillcolor=255, expand=False)
        noise = Image.effect_noise(img.size, 18).point(lambda v: 255 if v > 90 else 200)
        img = Image.composite(img, noise, img.point(lambda v: 255 if v < 250 else 0)).filter(ImageFilter.GaussianBlur(0.6))
        pages.append(img.convert("RGB"))
    pages[0].save(dst, save_all=True, append_images=pages[1:], resolution=150)
    dst.with_suffix(".gt.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    rows, log = [], []
    for s in json.loads((ROOT / "sources.json").read_text()):
        sid, kind = s["id"], s["kind"]
        if kind == "pdf":
            dst = RAW / f"{sid}.pdf"
            if not dst.exists():
                dst.write_bytes(fetch(s["url"]))
        elif kind == "url":
            ext = ".md" if s["url"].endswith(".md") else ".html"
            dst = RAW / f"{sid}{ext}"
            if not dst.exists():
                dst.write_bytes(fetch(s["url"]))
        else:  # 개인 마크다운
            md = strip_front_matter(Path(s["path"]).expanduser().read_text(encoding="utf-8"))
            if s.get("as") == "pptx":
                dst = RAW / f"{sid}.pptx"
                to_pptx(md, s["title"], dst)
            elif s.get("as") == "scan":
                dst = RAW / f"{sid}.scan.pdf"
                to_scan(md, dst)
            else:
                dst = RAW / f"{sid}.md"
                dst.write_text(Path(s["path"]).expanduser().read_text(encoding="utf-8"), encoding="utf-8")  # 앞머리(요약·파이프라인)도 본문이라 보존
        rows.append(f"| {sid} | {s['group']} | {dst.name} | {s['title']} | {s.get('url') or s.get('path')} |")
        log.append(f"- {dt.date.today()} ingest {sid} → raw/{dst.name} ({dst.stat().st_size:,} B)")
        print(sid, dst.name, dst.stat().st_size)
    (ROOT / "wiki" / "index.md").write_text(
        "# 원문 색인\n\nRAG 는 이 원문(raw/)을 파싱해 색인한다. 위키 요약 페이지는 색인 대상이 아니다.\n\n"
        "| id | 묶음 | 파일 | 제목 | 출처 |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n", encoding="utf-8")
    with open(ROOT / "wiki" / "log.md", "a", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
