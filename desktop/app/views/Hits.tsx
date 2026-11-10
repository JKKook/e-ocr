import type { Hit } from "../lib/api";

const VIA: Record<string, string> = {
  "text-layer": "PDF 텍스트", pptx: "PPTX", md: "마크다운", html: "웹",
  ocr_vlm: "OCR · qwen2.5vl", ocr_rapidocr: "OCR · RapidOCR",
};

export function Hits({ hits, showCtx = true }: { hits: Hit[]; showCtx?: boolean }) {
  return (
    <div>
      {hits.map((h, i) => (
        <div className="hit" key={h.id}>
          <div className="head">
            <b>[{i + 1}]</b>
            <code>{h.doc}</code>
            <span>p.{h.page}</span>
            {h.via && <span className={`pill ${h.via.startsWith("ocr") ? "warn" : ""}`}>{VIA[h.via] ?? h.via}</span>}
            <span style={{ flex: 1 }} />
            <code>{h.score.toFixed(3)}</code>
          </div>
          {showCtx && h.context && <div className="ctx">{h.context}</div>}
          <div className="txt">{h.text}</div>
        </div>
      ))}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return <div className="muted"><span className="spin" />{label}</div>;
}
