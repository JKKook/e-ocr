"use client";
import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";

// PDF.js 는 브라우저 전용(DOMMatrix 등)이라 정적 빌드 때 불러오지 않도록 효과 안에서만 import 한다.
type PdfJs = typeof import("pdfjs-dist");
let lib: Promise<PdfJs> | null = null;
const pdfjs = () => (lib ??= import("pdfjs-dist").then((m) => { m.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs"; return m; }));
const norm = (s: string) => s.replace(/\s+/g, "").toLowerCase();

type Props = { url: string; zoom: number; target: { page: number; text: string; term?: string } | null; ocrPages: Set<number>; onReady: (n: number) => void };

/** PDF 원문 뷰어 — 쪽마다 캔버스 + 텍스트 레이어(원문 위에서 바로 글자 선택). 보이는 쪽만 그린다. */
export function PdfViewer({ url, zoom, target, ocrPages, onReady }: Props) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [ratios, setRatios] = useState<number[]>([]);
  const [err, setErr] = useState("");
  const [width, setWidth] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const m = await pdfjs();
        const d = await m.getDocument({ url }).promise;
        const rs: number[] = [];
        for (let i = 1; i <= d.numPages; i++) { const v = (await d.getPage(i)).getViewport({ scale: 1 }); rs.push(v.height / v.width); }
        if (live) { setDoc(d); setRatios(rs); onReady(d.numPages); }
      } catch (e) { if (live) setErr(String(e)); }
    })();
    return () => { live = false; };
  }, [url, onReady]);

  useEffect(() => {  // 창 폭에 맞춰 쪽 폭을 정한다(폭 맞춤 × 확대율)
    const el = box.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.min(el.clientWidth - 48, 1100)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const w = Math.round(width * zoom / 100);
  return (
    <div ref={box} className="pdfpages">
      {err && <div className="note warn">PDF 를 그리지 못했어요({err}). 위에서 ‘읽어 낸 글자’로 볼 수 있어요.</div>}
      {!doc && !err && <div className="muted"><span className="spin" />원문을 여는 중…</div>}
      {doc && w > 0 && ratios.map((r, i) => (
        <PdfPage key={i + 1} doc={doc} n={i + 1} width={w} height={Math.round(w * r)} ocr={ocrPages.has(i + 1)}
          hl={target?.page === i + 1 ? target.text : ""} term={target?.page === i + 1 ? target.term : undefined} hot={target?.page === i + 1} />
      ))}
    </div>
  );
}

function PdfPage({ doc, n, width, height, hl, term, hot, ocr }: { doc: PDFDocumentProxy; n: number; width: number; height: number; hl: string; term?: string; hot: boolean; ocr: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  const [drawn, setDrawn] = useState(0);

  useEffect(() => {  // 화면 근처에 오면 그리기 시작
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) setSeen(true); }, { rootMargin: "900px 0px" });
    if (el.current) io.observe(el.current);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!seen || !canvas.current || !layer.current) return;
    let task: RenderTask | null = null, live = true;
    (async () => {
      const m = await pdfjs();
      const page: PDFPageProxy = await doc.getPage(n);
      const scale = width / page.getViewport({ scale: 1 }).width;
      const vp = page.getViewport({ scale });
      const dpr = window.devicePixelRatio || 1;
      const c = canvas.current!;
      c.width = Math.floor(vp.width * dpr); c.height = Math.floor(vp.height * dpr);
      task = page.render({ canvas: c, canvasContext: c.getContext("2d")!, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined });
      await task.promise.catch(() => {});
      if (!live || !layer.current) return;
      const L = layer.current;
      L.replaceChildren();
      L.style.setProperty("--total-scale-factor", String(scale));
      await new m.TextLayer({ textContentSource: page.streamTextContent(), container: L, viewport: vp }).render();
      if (live) setDrawn((x) => x + 1);
    })();
    return () => { live = false; task?.cancel(); };
  }, [seen, doc, n, width]);

  useEffect(() => {  // 근거 구절 강조 — 청크 글자에 들어 있는 텍스트 조각(5자 이상)을 칠한다
    const spans = layer.current?.querySelectorAll<HTMLSpanElement>("span:not(.markedContent)") ?? [];
    const t = norm(hl), w = norm(term ?? "");
    let first: HTMLSpanElement | null = null;
    spans.forEach((s) => {
      const st = norm(s.textContent ?? "");
      // 낱말(보고서 키워드)이 오면 그 낱말이 든 조각만, 아니면 근거 청크에 든 조각(5자 이상)
      const on = w ? st.includes(w) : !!t && st.length >= 5 && t.includes(st);
      s.classList.toggle("hl", on);
      if (on && !first) first = s;
    });
    if (first) (first as HTMLSpanElement).scrollIntoView({ behavior: "smooth", block: "center" });
  }, [hl, term, drawn]);

  return (
    <div ref={el} data-page={n} className={`rpage pdf ${hot ? "hot" : ""}`} style={{ width, height }}>
      <canvas ref={canvas} style={{ width, height }} aria-label={`${n}쪽`} />
      <div ref={layer} className="textLayer" style={{ "--scale-round-x": "1px", "--scale-round-y": "1px" } as React.CSSProperties} />
      <span className="pno">{n}쪽{ocr ? " · 스캔(OCR)" : ""}</span>
    </div>
  );
}
