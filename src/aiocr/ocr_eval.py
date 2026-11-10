"""OCR 품질 — 스캔 문서의 정답 텍스트(.gt.txt) 대비 엔진별 CER(문자 오류율, 공백 무시)과 페이지당 시간."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def norm(s: str) -> str:
    return "".join(s.split())


def cer(ref: str, hyp: str) -> float:
    ref, hyp = norm(ref), norm(hyp)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def main() -> dict:
    out = {}
    for gt in sorted((ROOT / "wiki" / "raw").glob("*.gt.txt")):
        sid = gt.name.split(".")[0]
        for engine in ("rapidocr", "vlm"):
            f = ROOT / "data" / "parsed" / engine / f"{sid}.jsonl"
            if not f.exists():
                continue
            blocks = [json.loads(l) for l in f.open()]
            hyp = "\n".join(b["text"] for b in blocks)
            out.setdefault(sid, {})[engine] = {"cer": round(cer(gt.read_text(), hyp), 4),
                                               "sec_per_page": round(sum(b.get("ocr_sec", 0) for b in blocks) / len(blocks), 2)}
    (ROOT / "reports" / "ocr_eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    main()
