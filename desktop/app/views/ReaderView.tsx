"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { getDoc, pageImage, rawUrl, type Doc, type DocDetail, type Hit } from "../lib/api";
import { ChatView, type Conv, type Quote } from "./ChatView";
import { Icon, docIcon } from "./icons";
import { PdfViewer } from "./PdfViewer";
import { ReportPanel } from "./ReportPanel";
import { PolicyLegend, TranscriptLines, usePolicy } from "./Transcript";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const TYPE: Record<string, string> = { video: "영상", pdf: "PDF", png: "사진", jpg: "사진", jpeg: "사진", pptx: "슬라이드", md: "문서", txt: "문서", html: "웹 문서", htm: "웹 문서" };
const at = (url: string, s: number) => `${url}${url.includes("?") ? "&" : "?"}t=${Math.floor(s)}s`;

type Props = {
  docId: string; startPage?: number; autoSummary?: boolean; docs: Doc[]; conv: Conv; onChange: (c: Conv) => void;
  refreshDocs: () => void; onBack: () => void; onManageDocs: () => void; onOpenDoc?: (id: string, page?: number) => void;
};

/** 읽기 화면 — 왼쪽 문서 정보·쪽 목록 | 가운데 원문(쪽 이미지·읽어 낸 글자·영상 대본) | 오른쪽 이 문서와 대화. */
export function ReaderView({ docId, startPage, autoSummary, docs, conv, onChange, refreshDocs, onBack, onManageDocs, onOpenDoc }: Props) {
  const [d, setD] = useState<DocDetail | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"image" | "text" | "report">("image");
  const [hitIds, setHitIds] = useState<string[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [zoom, setZoom] = useState(100);
  const [cur, setCur] = useState(startPage ?? 1);
  const [target, setTarget] = useState<{ page: number; text: string; term?: string } | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [sel, setSel] = useState<{ x: number; y: number; q: Quote } | null>(null);
  const view = useRef<HTMLDivElement>(null);
  const [pdfN, setPdfN] = useState(0);
  const policy = usePolicy();
  const [ver, setVer] = useState(0);  // 대본 분류를 고쳐 다시 색인되면 문서를 다시 읽는다  // PDF 뷰어가 쪽을 다 배치하면 쪽 번호 관찰을 다시 건다
  const onPdfReady = useCallback((n: number) => setPdfN(n), []);

  useEffect(() => {
    let live = true;
    getDoc(docId).then((x) => { if (live) { setD(x); if (!x.pages.some((p) => p.image)) setTab("text"); } }).catch((e) => setErr(String(e)));
    return () => { live = false; };
  }, [docId, ver]);

  const go = (page: number, smooth = true) =>
    view.current?.querySelector(`[data-page="${page}"]`)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  const curRef = useRef(cur);
  useEffect(() => { curRef.current = cur; }, [cur]);
  const switchTab = (t: "image" | "text" | "report") => { const keep = curRef.current; setTab(t); setTimeout(() => go(keep, false), 30); };  // 보기를 바꿔도 보던 쪽 유지
  useEffect(() => { if (d && startPage) setTimeout(() => go(startPage), 50); }, [d, startPage]);

  // 지금 보고 있는 쪽 번호
  useEffect(() => {
    const root = view.current;
    if (!d || !root) return;
    const io = new IntersectionObserver((es) => {
      const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (v) setCur(Number((v.target as HTMLElement).dataset.page));
    }, { root, threshold: 0.3 });
    root.querySelectorAll("[data-page]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [d, tab, pdfN]);

  const label = (p: number) => (d?.type === "video" ? mmss(d.pages.find((x) => x.page === p)?.t0 ?? 0) : `${p}쪽`);
  function jump(page: number, text = "", term?: string) {  // 보고서 항목 → 원문 그 쪽(형광펜)
    const back = d?.pages.some((p) => p.image) ? "image" : "text";
    setTab(back);
    setTarget({ page, text, term });
    setTimeout(() => go(page), 60);
    setTimeout(() => setTarget((t) => (t?.page === page ? { ...t, text: "", term: undefined } : t)), 8000);
  }
  function onCite(h: Hit) {
    setTarget({ page: h.page, text: h.text });
    go(h.page);
    setTimeout(() => setTarget((t) => (t?.page === h.page ? { ...t, text: "" } : t)), 6000);
  }
  function onMouseUp() {
    const s = window.getSelection();
    const text = s?.toString().trim() ?? "";
    const el = s?.anchorNode?.parentElement?.closest("[data-page]") as HTMLElement | null;
    if (text.length < 6 || !el || !s?.rangeCount) { setSel(null); return; }
    const r = s.getRangeAt(0).getBoundingClientRect();
    const box = view.current!.getBoundingClientRect();
    setSel({ x: r.left - box.left + r.width / 2, y: r.top - box.top + view.current!.scrollTop - 8, q: { page: Number(el.dataset.page), label: label(Number(el.dataset.page)), text } });
  }

  if (err) return <div className="content"><div className="panel err">문서를 열지 못했어요: {err} <button className="btn" onClick={onBack}>돌아가기</button></div></div>;
  if (!d) return <div className="content"><div className="panel muted"><span className="spin" />문서를 여는 중…</div></div>;
  const hasImg = d.pages.some((p) => p.image);
  const isPdf = d.type === "pdf";
  const ocrPages = new Set(d.pages.filter((p) => p.via.startsWith("ocr")).map((p) => p.page));
  const where = d.url ?? d.path ?? "";

  return (
    <div className="reader">
      <aside className="rinfo" aria-label="문서 정보">
        <button className="ibtn back" onClick={onBack}><Icon name="send" size={15} style={{ transform: "rotate(180deg)" }} /><span>돌아가기</span></button>
        <div className="kind"><Icon name={docIcon(d.type)} size={15} />{TYPE[d.type] ?? "문서"}</div>
        <h2>{d.title}</h2>
        <dl>
          <dt>분량</dt><dd>{d.type === "video" ? `${mmss(d.duration ?? 0)} · 대본 ${d.pages.length}구간` : `${d.pages.length}쪽`}</dd>
          {d.ocr_pages > 0 && <><dt>OCR</dt><dd>스캔 {d.ocr_pages}쪽을 글자로 읽음</dd></>}
          {d.pages[0]?.via.startsWith("transcript") && <><dt>대본</dt><dd>{d.pages[0].via === "transcript_stt" ? "음성을 받아 적음(STT)" : "영상 자막"}</dd></>}
          <dt>출처</dt><dd className="src">{d.url ? <a href={d.url} target="_blank" rel="noreferrer">{new URL(d.url).hostname}</a> : where.replace("upload:", "올린 파일 · ")}</dd>
          <dt>추가</dt><dd>{d.group === "user" ? "내가 추가" : "기본 문서"}</dd>
        </dl>
        <div className="toc" aria-label="쪽 목록">
          {d.pages.map((p) => (
            <button key={p.page} className={p.page === cur ? "on" : ""} onClick={() => go(p.page)}>
              <span>{label(p.page)}</span>
              {p.via.startsWith("ocr") && <span className="tag">OCR</span>}
              <span className="muted">{p.text.slice(0, 28)}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="rview" aria-label="원문">
        <div className="rtool">
          {hasImg && (
            <div className="seg" role="group" aria-label="보기 방식">
              <button className={tab === "image" ? "on" : ""} onClick={() => switchTab("image")}>{isPdf ? "원문 PDF" : "원문"}</button>
              <button className={tab === "text" ? "on" : ""} onClick={() => switchTab("text")}>읽어 낸 글자</button>
              <button className={tab === "report" ? "on" : ""} onClick={() => setTab("report")}>보고서</button>
            </div>
          )}
          {!hasImg && (
            <div className="seg" role="group" aria-label="보기 방식">
              <button className={tab !== "report" ? "on" : ""} onClick={() => switchTab("text")}>{d.type === "video" ? "대본" : "본문"}</button>
              <button className={tab === "report" ? "on" : ""} onClick={() => setTab("report")}>보고서</button>
            </div>
          )}
          <span className="muted mono">{label(cur)} / {d.type === "video" ? mmss(d.duration ?? 0) : `${d.pages.length}쪽`}</span>
          <span style={{ flex: 1 }} />
          {tab === "image" && hasImg && (
            <div className="seg" role="group" aria-label="확대">
              <button onClick={() => setZoom((z) => Math.max(50, z - 15))} aria-label="축소">−</button>
              <button onClick={() => setZoom(100)}>{zoom}%</button>
              <button onClick={() => setZoom((z) => Math.min(200, z + 15))} aria-label="확대">＋</button>
            </div>
          )}
          {d.type === "video" && d.url && <a className="ibtn" href={d.url} target="_blank" rel="noreferrer"><Icon name="play" size={15} /><span>영상 열기</span></a>}
        </div>
        {(target?.text || target?.term) && tab === "image" && hasImg && (!isPdf || ocrPages.has(target.page)) && (
          <div className="note warn cite-note">
            {label(target.page)}에서 찾은 근거예요. <button className="linkbtn" onClick={() => switchTab("text")}>글자에서 강조해 보기</button>
          </div>
        )}
        {tab === "report" && (
          <div className="rpages rp-wrap">
            <ReportPanel docs={[d.id]} searchIds={hitIds} onAsk={(q) => setPending(q)} onOpenOther={onOpenDoc ? (doc, page) => onOpenDoc(doc, page) : undefined}
              onJump={(_doc, page, text, term) => jump(page, text ?? "", term)} />
          </div>
        )}
        <div className="rpages" ref={view} onMouseUp={onMouseUp} onScroll={() => setSel(null)} hidden={tab === "report"}>
          {d.type === "video" && tab !== "image" && <PolicyLegend p={policy} />}
          {isPdf && tab === "image" && <PdfViewer url={rawUrl(d.id)} zoom={zoom} target={target} ocrPages={ocrPages} onReady={onPdfReady} />}
          {!(isPdf && tab === "image") && d.pages.map((p) => {
            const hot = target?.page === p.page;
            if (tab === "image" && p.image) {
              return (
                <figure key={p.page} data-page={p.page} className={`rpage img ${hot ? "hot" : ""}`} style={{ width: `${zoom}%` }}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- 로컬 API 가 그린 쪽 이미지(정적 export) */}
                  <img loading="lazy" src={pageImage(d.id, p.page)} alt={`${d.title} ${p.page}쪽`} />
                  <figcaption>{p.page}쪽{p.via.startsWith("ocr") ? " · 스캔(OCR)" : ""}</figcaption>
                </figure>
              );
            }
            const needle = target?.term && p.text.includes(target.term) ? target.term : target?.text?.slice(0, 60) ?? "";
            const len = needle === target?.term ? needle.length : target?.text?.length ?? 0;
            const i = hot && needle ? p.text.indexOf(needle) : -1;
            return (
              <article key={p.page} data-page={p.page} className={`rpage txt ${hot ? "hot" : ""}`}>
                <header>
                  {d.type === "video" ? <>{mmss(p.t0 ?? 0)}–{mmss((p.t0 ?? 0) + 60)}{d.url && <a href={at(d.url, p.t0 ?? 0)} target="_blank" rel="noreferrer"> <Icon name="play" size={12} /> 이 구간 보기</a>}</>
                    : <>{p.page}쪽{p.via.startsWith("ocr") ? " · 스캔을 OCR 로 읽은 글자" : ""}</>}
                </header>
                {d.type === "video" && p.parts?.length ? (
                  <TranscriptLines doc={d.id} parts={p.parts} policy={policy} onReindexed={() => setVer((x) => x + 1)} />
                ) : <div className="body">
                  {i >= 0 ? <>{p.text.slice(0, i)}<mark>{p.text.slice(i, i + len)}</mark>{p.text.slice(i + len)}</> : p.text}
                </div>}
              </article>
            );
          })}
          {sel && (
            <button className="askbubble" style={{ left: sel.x, top: sel.y }} onMouseDown={(e) => e.preventDefault()}
              onClick={() => { setQuote(sel.q); setSel(null); window.getSelection()?.removeAllRanges(); }}>
              <Icon name="chat" size={14} />이 부분 묻기
            </button>
          )}
          {tab === "image" && hasImg && <p className="muted hint">{isPdf
            ? `원문에서 바로 글자를 드래그해 물어볼 수 있어요.${ocrPages.size ? " 스캔 쪽은 ‘읽어 낸 글자’에서 고르세요." : ""}`
            : "글자를 골라 물어보려면 위에서 ‘읽어 낸 글자’로 바꾸세요."}</p>}
        </div>
      </section>

      <section className="rchat" aria-label="이 문서와 대화">
        <div className="rchead"><Icon name="chat" size={16} /><b>이 문서와 대화</b></div>
        <ChatView conv={conv} onChange={onChange} docs={docs} refreshDocs={refreshDocs} onManageDocs={onManageDocs}
          side={{ doc: docs.find((x) => x.id === d.id) ?? { ...d, pages: d.pages.length }, onCite, quote, clearQuote: () => setQuote(null), autoSummary,
            onHits: (hs) => setHitIds(hs.map((h) => h.id)), pending, clearPending: () => setPending(null) }} />
      </section>
    </div>
  );
}
