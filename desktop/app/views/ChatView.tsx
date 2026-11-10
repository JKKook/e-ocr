"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { addUrl, chat, feedback, getJob, uploadFile, type Confidence, type Doc, type Done, type Hit, type Job } from "../lib/api";
import { Composer, DocPicker, jobStatus, stripUrls, urlsIn, type Attach } from "./Composer";
import { Icon, docIcon } from "./icons";
import { SourcePanel } from "./SourcePanel";

export type Msg =
  | { role: "user"; text: string; attached?: string[]; quote?: Quote }
  | { role: "assistant"; text: string; hits: Hit[]; confidence?: Confidence; done?: Done; status?: string;
      phase: "ingest" | "search" | "write" | "done" | "error"; fb?: 1 | -1 };
export type Conv = { id: string; title: string; scope: string[]; msgs: Msg[]; updated: number; docId?: string };
export type Quote = { page: number; label: string; text: string };

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export const where = (h: Hit) => (h.via.startsWith("transcript") ? `영상 ${mmss(h.t0 ?? 0)}` :
  `${h.via.startsWith("ocr") ? "스캔" : h.via === "text-layer" ? "PDF" : h.via === "pptx" ? "PPT" : h.via === "html" ? "웹" : "문서"} · ${h.page}쪽`);
const SUMMARIZE = "이 자료의 핵심을 요약해줘";

/** 같은 문서·같은 쪽 청크는 칩 하나로 묶고, 답에 인용된 출처를 앞에 둔다. */
function groups(hits: Hit[], text: string) {
  const cited = new Set([...text.matchAll(/\[(\d+)\]/g)].map((x) => Number(x[1])));
  const out: { key: string; nums: number[]; hits: Hit[]; cited: boolean }[] = [];
  hits.forEach((h, i) => {
    const key = `${h.doc}:${h.page}`;
    const g = out.find((x) => x.key === key) ?? out[out.push({ key, nums: [], hits: [], cited: false }) - 1];
    g.nums.push(i + 1); g.hits.push(h); g.cited ||= cited.has(i + 1);
  });
  return out.sort((a, b) => Number(b.cited) - Number(a.cited));
}

/** 답 속 [1] 을 눌러 볼 수 있는 출처 칩으로 바꾼다. */
function Cited({ text, hits, onOpen }: { text: string; hits: Hit[]; onOpen: (h: Hit) => void }) {
  return (
    <>
      {text.split(/(\[\d+\])/g).map((p, i) => {
        const n = /^\[(\d+)\]$/.exec(p)?.[1];
        const h = n ? hits[Number(n) - 1] : undefined;
        return h ? <button key={i} className="cite" aria-label={`출처 ${n}: ${h.title} ${where(h)}`} onClick={() => onOpen(h)}>{n}</button> : <Fragment key={i}>{p}</Fragment>;
      })}
    </>
  );
}

type Props = {
  conv: Conv; onChange: (c: Conv) => void; docs: Doc[]; refreshDocs: () => void; onManageDocs: () => void;
  onOpenDoc?: (id: string, page?: number, autoSummary?: boolean) => void;
  // 읽기 화면 오른쪽 패널로 쓸 때: 범위 고정 · 인용은 원문 쪽으로 이동 · 원문에서 고른 구절을 붙여 묻기
  side?: { doc: Doc; onCite: (h: Hit) => void; quote: Quote | null; clearQuote: () => void; autoSummary?: boolean;
    onHits?: (hs: Hit[]) => void; pending?: string | null; clearPending?: () => void };
};

export function ChatView({ conv, onChange, docs, refreshDocs, onManageDocs, onOpenDoc, side }: Props) {
  const [llm, setLlm] = useState<"local" | "claude">("local");
  const [open, setOpen] = useState<Hit | null>(null);
  const [attaches, setAttaches] = useState<Attach[]>([]);
  const [picker, setPicker] = useState(false);
  const [drag, setDrag] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const cur = useRef(conv);
  const att = useRef(attaches);
  useEffect(() => { cur.current = conv; }, [conv]);
  useEffect(() => { att.current = attaches; }, [attaches]);
  const last = conv.msgs.at(-1);
  const busy = last?.role === "assistant" && ["ingest", "search", "write"].includes(last.phase);

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [conv.msgs]);
  const auto = useRef(false);
  useEffect(() => {
    if (side?.autoSummary && !auto.current && conv.msgs.length === 0) { auto.current = true; ask(SUMMARIZE); }
    if (side?.pending && !busy) { const q = side.pending; side.clearPending?.(); ask(q); }  // 보고서의 읽기 질문
  });

  // 올린 자료의 수집 진행을 1초마다 갱신한다.
  const pendingIds = attaches.filter((a) => a.job && (a.job.state === "queued" || a.job.state === "running")).map((a) => a.job!.id).join(",");
  useEffect(() => {
    if (!pendingIds) return;
    const t = setInterval(async () => {
      const prev = att.current;
      const next = await Promise.all(prev.map(async (a) => (a.job && (a.job.state === "queued" || a.job.state === "running") ? { ...a, job: await getJob(a.job.id).catch(() => a.job) } : a)));
      if (next.some((a, i) => a.job?.state === "ok" && prev[i]?.job?.state !== "ok")) refreshDocs();
      att.current = next;
      setAttaches(next);
    }, 1000);
    return () => clearInterval(t);
  }, [pendingIds, refreshDocs]);

  const save = (f: (c: Conv) => Conv) => { const c = f(cur.current); cur.current = c; onChange(c); };
  const patch = (f: (m: Extract<Msg, { role: "assistant" }>) => Partial<Extract<Msg, { role: "assistant" }>>) =>
    save((c) => { const l = c.msgs.at(-1); return l?.role === "assistant" ? { ...c, msgs: [...c.msgs.slice(0, -1), { ...l, ...f(l) }], updated: Date.now() } : c; });
  const setAtt = (f: (as: Attach[]) => Attach[]) => { att.current = f(att.current); setAttaches(att.current); };

  const addAttach = (kind: Attach["kind"], label: string, p: Promise<Job>) => {
    const key = `${Date.now()}-${Math.random()}`;
    setAtt((as) => [...as, { key, kind, label }]);
    p.then((job) => setAtt((as) => as.map((a) => (a.key === key ? { ...a, job } : a))))
      .catch((e) => setAtt((as) => as.map((a) => (a.key === key ? { ...a, error: e instanceof Error ? e.message : String(e) } : a))));
  };
  const attachUrl = (u: string) => { if (!att.current.some((a) => a.label === u)) addAttach("url", u, addUrl(u)); };
  const attachFiles = (fs: File[]) => fs.forEach((f) => addAttach("file", f.name, uploadFile(f)));

  const [opening, setOpening] = useState(false);
  async function waitAttaches(quiet = false): Promise<string[]> {
    for (;;) {
      const as = att.current;
      const pending = as.filter((a) => !a.error && a.job?.state !== "ok" && a.job?.state !== "error");
      if (!pending.length) return as.filter((a) => a.job?.state === "ok").map((a) => a.job!.result!.id);
      if (!quiet) patch(() => ({ status: `자료를 읽는 중 — ${pending.map((a) => `${a.job?.result?.title ?? a.label.slice(0, 40)}: ${jobStatus(a.job)}`).join(" · ")}` }));
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  async function ask(raw: string, scopeOverride?: string[]) {
    if (busy) return;
    if (side) scopeOverride = [side.doc.id];
    const quote = side?.quote ?? undefined;
    urlsIn(raw).forEach(attachUrl);  // 질문 속 링크도 자료로
    const hasAttach = att.current.length > 0;
    // 질문 없이 자료만 보내면: 준비되는 대로 뷰어로 열고 오른쪽에서 요약을 시작한다
    if (hasAttach && !stripUrls(raw) && onOpenDoc && !side) {
      setOpening(true);
      const ids = await waitAttaches(true);
      setOpening(false);
      if (ids.length) { setAtt(() => []); onOpenDoc(ids[0], undefined, true); }
      return;
    }
    const typed = stripUrls(raw) || (hasAttach ? SUMMARIZE : quote ? "이 부분을 쉽게 풀어 설명해줘" : "");
    if (!typed) return;
    // 원문에서 고른 구절은 질문 앞에 붙여 검색·생성 모두에 쓰이게 한다
    const q = quote ? `「${quote.text.slice(0, 500)}」(${quote.label}) — ${typed}` : typed;
    side?.clearQuote();
    const history = cur.current.msgs.filter((m) => m.role === "user" || m.phase === "done").slice(-4).map((m) => ({ role: m.role, text: m.text }));
    const labels = att.current.map((a) => a.job?.result?.title ?? a.label);
    save((c) => ({ ...c, title: c.msgs.length ? c.title : q.slice(0, 40), updated: Date.now(),
      msgs: [...c.msgs, { role: "user", text: typed, attached: labels, quote }, { role: "assistant", text: "", hits: [], phase: hasAttach ? "ingest" : "search" }] }));

    let scope = scopeOverride ?? cur.current.scope;
    if (hasAttach) {
      const ids = await waitAttaches();
      const failed = att.current.filter((a) => a.error || a.job?.state === "error");
      setAtt(() => []);
      if (!ids.length) {
        patch(() => ({ phase: "error", status: undefined, text: `자료를 추가하지 못했어요: ${failed.map((a) => a.error ?? a.job?.error).join(" / ")}` }));
        return;
      }
      scope = [...new Set([...(scopeOverride ?? []), ...ids])];  // 이번에 올린 자료에서 답한다
      patch(() => ({ phase: "search", status: undefined }));
    }
    save((c) => ({ ...c, scope }));
    try {
      await chat(q, history, llm, {
        hits: (d) => { side?.onHits?.(d.hits); patch(() => ({ hits: d.hits, confidence: d.confidence, phase: d.confidence === "none" ? "search" : "write" })); },
        token: (tok) => patch((m) => ({ text: m.text + tok })),
        replace: (full) => patch(() => ({ text: full })),
        done: (d) => patch(() => ({ text: d.answer, done: d, phase: "done" })),
        error: (e) => patch(() => ({ text: `문제가 생겼어요: ${e}`, phase: "error" })),
      }, scope);
    } catch (e) {
      patch(() => ({ text: `서버에 연결하지 못했어요 (${e})`, phase: "error" }));
    }
  }

  async function rate(i: number, v: 1 | -1) {
    const m = conv.msgs[i];
    if (m.role !== "assistant" || !m.done?.trace_id) return;
    save((c) => ({ ...c, msgs: c.msgs.map((x, j) => (j === i ? ({ ...x, fb: v } as Msg) : x)) }));
    try { await feedback(m.done.trace_id, v); } catch { /* 기록 실패는 조용히 */ }
  }

  const composer = (big: boolean) => (
    <Composer big={big} busy={busy || opening} llm={llm} setLlm={setLlm} inputRef={input} fixed={side ? side.doc : undefined}
      quote={side?.quote ?? null} onClearQuote={side?.clearQuote} onOpenAttach={onOpenDoc && !side ? (id) => onOpenDoc(id) : undefined}
      attaches={attaches} onAttachUrl={attachUrl} onAttachFiles={attachFiles}
      onRemoveAttach={(k) => setAtt((as) => as.filter((a) => a.key !== k))}
      scope={conv.scope} docs={docs} onPickScope={() => setPicker(true)} onClearScope={() => save((c) => ({ ...c, scope: [] }))}
      onSend={(t) => ask(t)} />
  );
  const recent = [...docs].sort((a, b) => b.added - a.added).slice(0, 6);
  const empty = conv.msgs.length === 0;
  const openCite = (h: Hit) => (side ? side.onCite(h) : setOpen(h));

  return (
    <div className={`chat ${open ? "with-panel" : ""}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDrag(false); }}
      onDrop={(e) => { e.preventDefault(); setDrag(false); attachFiles(Array.from(e.dataTransfer.files)); }}>
      {empty && side ? (
        <>
        <div className="thread sidehome">
          <div className="doccard">
            <div className="row"><Icon name={docIcon(side.doc.type)} /><b className="t">{side.doc.title}</b></div>
            <div className="muted">{side.doc.type === "video" ? "영상 대본" : `${side.doc.pages}쪽`}{side.doc.ocr_pages ? ` · 스캔 ${side.doc.ocr_pages}쪽 OCR` : ""} — 이 문서 안에서만 찾아 답해요.</div>
          </div>
          <div className="quick">
            <button className="btn" onClick={() => ask(SUMMARIZE)}><Icon name="sum" size={15} />전체 요약</button>
            <button className="btn" onClick={() => ask("이 문서에 나오는 중요한 숫자와 그 의미를 정리해줘")}><Icon name="search" size={15} />핵심 숫자 정리</button>
          </div>
          <p className="muted hint">원문에서 글을 드래그하면 그 부분을 붙여 물어볼 수 있어요.</p>
        </div>
        <div className="dock">{composer(false)}</div>
        </>
      ) : empty ? (
        <div className="home">
          <h2>무엇이 궁금하세요?</h2>
          <p className="muted">질문만 쓰면 문서 {docs.length}개에서 찾아 근거와 함께 답해요. 웹·PDF·유튜브 링크를 붙여 넣거나 파일을 끌어다 놓으면 그 자료로 바로 답해요.</p>
          {composer(true)}
          {recent.length > 0 && (
            <section className="recent" aria-label="최근 문서">
              <div className="rhead"><b>최근 문서</b><button className="linkbtn" onClick={onManageDocs}>문서함 열기</button></div>
              <div className="rgrid">
                {recent.map((d) => (
                  <div key={d.id} className="rcard">
                    <Icon name={docIcon(d.type)} />
                    <div className="rt">
                      {onOpenDoc ? <button className="t linkish" title={`${d.title} 열기`} onClick={() => onOpenDoc(d.id)}>{d.title}</button> : <div className="t" title={d.title}>{d.title}</div>}
                      <div className="muted">{d.type === "video" ? "영상" : `${d.pages}쪽`}{d.ocr_pages ? ` · 스캔 ${d.ocr_pages}쪽` : ""} · {d.group === "user" ? "내가 추가" : "기본"}</div>
                    </div>
                    <div className="ract">
                      {onOpenDoc && <button className="btn" onClick={() => onOpenDoc(d.id)}><Icon name="file" size={15} />열기</button>}
                      <button className="btn" onClick={() => ask(SUMMARIZE.replace("이 자료", `'${d.title}'`), [d.id])}><Icon name="sum" size={15} />요약</button>
                      <button className="btn" onClick={() => { save((c) => ({ ...c, scope: [d.id] })); input.current?.focus(); }}><Icon name="chat" size={15} />묻기</button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      ) : (
        <>
          <div className="thread">
            {conv.msgs.map((m, i) => m.role === "user" ? (
              <div key={i} className="msg user">
                <div className="bubble">
                  {m.quote && <div className="quoteline"><b>원문 {m.quote.label}</b>{m.quote.text.length > 160 ? m.quote.text.slice(0, 160) + "…" : m.quote.text}</div>}
                  {m.attached?.length ? <div className="atline">{m.attached.map((a) => <span key={a}><Icon name="clip" size={13} />{a.length > 50 ? a.slice(0, 50) + "…" : a}</span>)}</div> : null}
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={i} className="msg bot">
                {(m.phase === "ingest" || (m.phase === "search" && m.confidence !== "none")) && (
                  <div className="muted" role="status"><span className="spin" />{m.status ?? "문서에서 찾는 중…"}</div>
                )}
                {m.confidence === "low" && m.phase !== "error" && !conv.scope.length && <div className="note warn">관련 문서가 확실하지 않아요. 아래 출처를 꼭 확인해 주세요.</div>}
                {(m.text || m.phase === "write") && (
                  <div className="answer"><Cited text={m.text} hits={m.hits} onOpen={openCite} />{m.phase === "write" && <span className="caret" />}</div>
                )}
                {m.confidence === "none" && m.phase === "done" && (
                  <div className="row" style={{ marginTop: 8 }}>
                    <button className="btn" onClick={() => input.current?.focus()}><Icon name="link" size={15} />링크로 자료 추가</button>
                    <button className="btn" onClick={() => setPicker(true)}><Icon name="stack" size={15} />찾을 문서 고르기</button>
                  </div>
                )}
                {m.done && Object.keys(m.done.issues).length > 0 && <div className="note warn">일부 내용(특히 숫자)을 근거 문서에서 확인하지 못했어요. 출처와 함께 확인해 주세요.</div>}
                {m.done?.llm === "local→claude" && <div className="note">정확도를 위해 Claude 로 다시 답했어요.</div>}
                {m.hits.length > 0 && m.confidence !== "none" && (m.phase === "done" || m.phase === "write") && (
                  <div className="sources">
                    {groups(m.hits, m.text).map((g) => (
                      <button key={g.key} className={`src ${g.cited ? "cited" : "dim"} ${open && g.hits.some((h) => h.id === open.id) ? "on" : ""}`}
                        onClick={() => openCite(g.hits[0])} aria-label={`${g.cited ? "답에 쓰인 출처" : "함께 찾은 문서"}: ${g.hits[0].title} ${where(g.hits[0])}`}>
                        <span className="n">{g.nums.join("·")}</span>
                        <span className="t">{g.hits[0].title}</span>
                        <span className="muted">{where(g.hits[0])}</span>
                      </button>
                    ))}
                  </div>
                )}
                {m.phase === "done" && m.done?.trace_id && m.confidence !== "none" && (
                  <div className="fb">
                    <span className="muted">도움이 됐나요?</span>
                    <button className={m.fb === 1 ? "on" : ""} onClick={() => rate(i, 1)} aria-label="도움 됨" aria-pressed={m.fb === 1}><Icon name="up" size={15} /></button>
                    <button className={m.fb === -1 ? "on" : ""} onClick={() => rate(i, -1)} aria-label="도움 안 됨" aria-pressed={m.fb === -1}><Icon name="down" size={15} /></button>
                  </div>
                )}
              </div>
            ))}
            <div ref={end} />
          </div>
          <div className="dock">{composer(false)}</div>
        </>
      )}

      {drag && <div className="dropzone" aria-hidden="true"><Icon name="clip" size={28} />놓으면 자료로 추가하고, 보내면 그 자료로 답해요</div>}
      {open && !side && <SourcePanel key={open.id} hit={open} doc={docs.find((d) => d.id === open.doc)} onClose={() => setOpen(null)}
        onOpenDoc={onOpenDoc ? () => onOpenDoc(open.doc, open.page) : undefined} />}
      {picker && <DocPicker docs={docs} scope={conv.scope} onClose={() => setPicker(false)}
        onApply={(ids) => { save((c) => ({ ...c, scope: ids })); setPicker(false); input.current?.focus(); }} />}
    </div>
  );
}
