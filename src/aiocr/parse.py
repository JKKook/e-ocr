"""파싱 — wiki/raw/ 원문을 페이지·슬라이드 단위 블록으로 바꿔 data/parsed/<ocr>/<id>.jsonl 에 쓴다.

텍스트 레이어가 없는 PDF 페이지(글자 < 20자)만 OCR 로 보낸다. OCR 엔진은 둘:
  rapidocr — PP-OCRv5 한국어 모바일(ONNX), 줄 단위 검출·인식
  vlm      — Ollama qwen2.5vl:7b 에 페이지 이미지를 주고 글자를 그대로 옮기게 함
사용: python -m aiocr.parse [rapidocr|vlm]
"""
import base64
import json
import sys
import time
import urllib.request
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "wiki" / "raw"
OLLAMA = "http://127.0.0.1:11434/api/generate"
VLM_PROMPT = ("이 문서 이미지의 글자를 위에서 아래로 줄 단위로 빠짐없이 그대로 옮겨 적어라. "
              "요약·설명·번역 금지, 보이는 글자만 출력.")
_rapid = None


def ocr_rapid(png: bytes) -> str:
    global _rapid
    if _rapid is None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        _rapid = RapidOCR(params={"Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV5,
                                  "Rec.model_type": ModelType.MOBILE})
    res = _rapid(png)
    if res.txts is None:
        return ""
    # 검출 상자를 위→아래, 같은 줄이면 왼→오른쪽으로 정렬해 줄 단위로 잇는다
    items = sorted(zip(res.boxes.tolist(), res.txts), key=lambda b: (round(b[0][0][1] / 15), b[0][0][0]))
    lines, last_y = [], None
    for box, txt in items:
        y = round(box[0][1] / 15)
        if last_y is not None and y == last_y:
            lines[-1] += " " + txt
        else:
            lines.append(txt)
        last_y = y
    return "\n".join(lines)


def ocr_vlm(png: bytes) -> str:
    body = json.dumps({"model": "qwen2.5vl:7b", "prompt": VLM_PROMPT, "images": [base64.b64encode(png).decode()],
                       "stream": False, "options": {"temperature": 0, "num_predict": 4096}}).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())["response"].strip()


def parse_pdf(path: Path, ocr) -> list[dict]:
    out = []
    for i, page in enumerate(pymupdf.open(path)):
        text = page.get_text().strip()
        if len(text) >= 20:
            out.append({"page": i + 1, "text": text, "via": "text-layer"})
            continue
        png = page.get_pixmap(dpi=150).tobytes("png")
        t0 = time.time()
        text = ocr(png)
        out.append({"page": i + 1, "text": text, "via": ocr.__name__, "ocr_sec": round(time.time() - t0, 2)})
    return out


def parse_pptx(path: Path) -> list[dict]:
    from pptx import Presentation
    out = []
    for i, slide in enumerate(Presentation(path).slides):
        parts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        out.append({"page": i + 1, "text": "\n".join(parts), "via": "pptx"})
    return out


def parse_html(path: Path) -> list[dict]:
    import trafilatura
    text = trafilatura.extract(path.read_text(encoding="utf-8", errors="ignore"), include_tables=True) or ""
    return [{"page": 1, "text": text, "via": "html"}]


def main(engine: str = "rapidocr") -> None:
    ocr = {"rapidocr": ocr_rapid, "vlm": ocr_vlm}[engine]
    dst = ROOT / "data" / "parsed" / engine
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(RAW.iterdir()):
        if f.name.endswith(".gt.txt"):
            continue
        sid = f.name.split(".")[0]
        if f.suffix == ".pdf":
            blocks = parse_pdf(f, ocr)
        elif f.suffix == ".pptx":
            blocks = parse_pptx(f)
        elif f.suffix == ".html":
            blocks = parse_html(f)
        else:
            blocks = [{"page": 1, "text": f.read_text(encoding="utf-8"), "via": "md"}]
        with open(dst / f"{sid}.jsonl", "w", encoding="utf-8") as w:
            for b in blocks:
                w.write(json.dumps({"doc": sid, **b}, ensure_ascii=False) + "\n")
        vias = {b["via"] for b in blocks}
        print(f"{sid:22} {len(blocks):3} blocks {sum(len(b['text']) for b in blocks):7} chars {sorted(vias)}")


if __name__ == "__main__":
    main(*sys.argv[1:])
