"use client";
import { useEffect, useRef, useState } from "react";
import { Icon } from "./icons";
import { addUrl, deleteDoc, getDocs, getJob, uploadFile, type Doc, type Job } from "../lib/api";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.pptx,.md,.txt,.html,.htm";
const TYPE: Record<string, string> = { video: "영상", pdf: "PDF", png: "사진", jpg: "사진", jpeg: "사진", pptx: "PPT", md: "문서", txt: "문서", html: "웹", htm: "웹" };
// 개발 단계 이름 → 사용자 말
const STEP: Record<string, string> = { 대기: "차례를 기다리는 중", 수집: "가져오는 중", 파싱: "글자를 읽는 중", 맥락: "내용을 정리하는 중", 색인: "검색할 수 있게 저장하는 중", 완료: "완료" };

function progress(j: Job): number {
  const order = ["대기", "수집", "파싱", "맥락", "색인", "완료"];
  if (j.state === "ok") return 100;
  const last = j.log.at(-1);
  const m = last?.stage === "맥락" ? /(\d+)\/(\d+)/.exec(last.msg) : null;
  const base = (Math.max(0, order.indexOf(j.stage)) / (order.length - 1)) * 100;
  return Math.round(m ? base + (Number(m[1]) / Number(m[2])) * 20 : base);
}

export function DocsView({ onChanged, onOpen, onCompare }: { onChanged: () => void; onOpen: (id: string) => void; onCompare: (ids: string[]) => void }) {
  const [pick, setPick] = useState<string[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [url, setUrl] = useState("");
  const [err, setErr] = useState("");
  const [drag, setDrag] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const running = jobs.some((j) => j.state === "queued" || j.state === "running");

  const reload = () => getDocs().then(setDocs).catch((e) => setErr(String(e)));
  useEffect(() => { reload(); }, []);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(async () => {
      const next = await Promise.all(jobs.map((j) => (j.state === "ok" || j.state === "error" ? j : getJob(j.id).catch(() => j))));
      setJobs(next);
      if (next.some((j, i) => j.state === "ok" && jobs[i].state !== "ok")) { reload(); onChanged(); }
    }, 1000);
    return () => clearInterval(t);
  }, [jobs, running, onChanged]);

  const start = async (p: Promise<Job>) => {
    setErr("");
    try { const j = await p; setJobs((js) => [j, ...js]); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const onFiles = (fs: FileList | null) => { for (const f of Array.from(fs ?? [])) start(uploadFile(f)); };

  async function remove(d: Doc) {
    if (!confirm(`'${d.title}' 문서를 지울까요? 이 문서로는 더 이상 답하지 않아요.`)) return;
    try { await deleteDoc(d.id); reload(); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <>
      <div className={`drop ${drag ? "on" : ""}`} onClick={() => file.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); onFiles(e.dataTransfer.files); }}>
        <div className="big">파일을 여기에 끌어다 놓거나, 눌러서 고르세요</div>
        <div className="muted">PDF · 스캔 PDF · 사진(PNG·JPG) · PPTX · 텍스트 · HTML — 30MB 까지. 사진과 스캔 문서는 글자를 읽어 냅니다.</div>
        <input ref={file} type="file" accept={ACCEPT} multiple hidden onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
      </div>
      <form className="ask" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if (url.trim()) { start(addUrl(url.trim())); setUrl(""); } }}>
        <input type="text" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="또는 웹 문서·PDF·유튜브 영상 주소 붙여넣기 — https://…" />
        <button className="btn" disabled={!/^https?:\/\//.test(url.trim())}>주소로 추가</button>
      </form>
      {err && <div className="note warn" style={{ marginTop: 10 }}>{err}</div>}

      {jobs.length > 0 && (
        <div className="panel" style={{ marginTop: 12 }}>
          {jobs.map((j) => (
            <div className="job" key={j.id}>
              <div className="row">
                <b className="t">{j.result?.title ?? j.label}</b>
                <span style={{ flex: 1 }} />
                {j.state === "error" ? <span className="pill warn">추가하지 못함</span>
                  : j.state === "ok" ? <span className="pill ok">추가됨 — 이제 질문할 수 있어요</span>
                    : <span className="muted">{STEP[j.stage] ?? j.stage}…</span>}
              </div>
              {j.state === "error" ? <div className="err" style={{ fontSize: 12.5 }}>{j.error}</div>
                : <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${progress(j)}%`, background: j.state === "ok" ? "var(--busy)" : undefined }} /></div>}
            </div>
          ))}
          {running && <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>스캔 쪽이 많으면 몇 분 걸릴 수 있어요. 다른 화면으로 가도 계속 진행돼요.</p>}
        </div>
      )}

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="row" style={{ marginBottom: 6 }}>
          <h3 style={{ margin: 0 }}>문서 {docs.length}개</h3>
          <span style={{ flex: 1 }} />
          <span className="muted small">{pick.length ? `${pick.length}개 고름` : "2~4개를 체크하면 비교 보고서"}</span>
          <button className="btn primary" disabled={pick.length < 2 || pick.length > 4} onClick={() => onCompare(pick)}><Icon name="sum" size={15} />비교 보고서</button>
        </div>
        <div className="doclist">
          {[...docs].reverse().map((d) => (
            <div className="doc" key={d.id}>
              <input type="checkbox" aria-label={`${d.title} 비교에 넣기`} checked={pick.includes(d.id)}
                onChange={() => setPick((p) => (p.includes(d.id) ? p.filter((x) => x !== d.id) : [...p, d.id].slice(-4)))} style={{ accentColor: "var(--accent)" }} />
              <span className="type">{TYPE[d.type] ?? "문서"}</span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <button className="t linkish" onClick={() => onOpen(d.id)} title={`${d.title} 열기`}>{d.title}</button>
                <div className="muted" style={{ fontSize: 12 }}>
                  {d.type === "video" ? `영상 ${Math.round((d.duration ?? 0) / 60)}분` : `${d.pages}쪽`}{d.ocr_pages ? ` · 스캔 ${d.ocr_pages}쪽 OCR` : ""} · {d.group === "user" ? "내가 추가" : "기본 문서"}
                  {d.url ? ` · ${new URL(d.url).hostname}` : ""}
                </div>
              </div>
              <button className="btn" onClick={() => onOpen(d.id)}>열기</button>
              {d.deletable && <button className="btn" onClick={() => remove(d)}>지우기</button>}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
