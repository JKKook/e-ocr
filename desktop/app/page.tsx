"use client";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { DEMO, getDocs, getReport, health, type Doc, type Report } from "./lib/api";
import { ChatView, type Conv } from "./views/ChatView";
import { CompareView } from "./views/CompareView";
import { DocsView } from "./views/DocsView";
import { EvalView } from "./views/EvalView";
import { GateView } from "./views/GateView";
import { Icon } from "./views/icons";
import { ReaderView } from "./views/ReaderView";
import { ReportPanel } from "./views/ReportPanel";
import { ReportView } from "./views/ReportView";
import { TraceView } from "./views/TraceView";

// 사용자 화면이 기본. 관리자 모드를 켜면 운영·평가 화면이 나온다.
const VIEWS = {
  chat: { admin: false, label: "대화", sub: "" },
  reader: { admin: false, label: "문서 읽기", sub: "" },
  compare: { admin: false, label: "비교 보고서", sub: "고른 문서들의 공통점과 차이" },
  docs: { admin: false, label: "문서함", sub: "파일·링크를 추가하고 관리해요" },
  trace: { admin: true, label: "질의 trace", sub: "단계별 지연 · 가드 · 피드백" },
  gate: { admin: true, label: "품질 게이트", sub: "기준선 대비 회귀 판정" },
  search: { admin: true, label: "검색 비교", sub: "dense · sparse · hybrid · rerank 나란히" },
  report: { admin: true, label: "성능 보고서", sub: "개념 채점표 · 30문항 실측 · 검색 ablation · OCR · 답변" },
  eval: { admin: true, label: "평가 문항", sub: "문항별 답변과 채점" },
} as const;
type View = keyof typeof VIEWS;
type Theme = "system" | "light" | "dark";

// ── 기기별 저장(대화·설정) — 정적 export 라 첫 렌더는 기본값, 그 뒤 localStorage. 저장이 막혀도 동작한다. ──
const subs = new Set<() => void>();
const subscribe = (cb: () => void) => { subs.add(cb); return () => subs.delete(cb); };
const cache = new Map<string, unknown>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
function read<T>(key: string, init: T): T {
  if (!cache.has(key)) {
    try { const s = localStorage.getItem(key); cache.set(key, s ? JSON.parse(s) : init); } catch { cache.set(key, init); }
  }
  return cache.get(key) as T;
}
function write<T>(key: string, v: T) {
  cache.set(key, v);
  subs.forEach((f) => f());
  clearTimeout(timers.get(key));  // 답이 흘러나오는 동안 매 토큰마다 쓰지 않게 묶는다
  timers.set(key, setTimeout(() => { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* 저장소 없음 */ } }, 400));
}
function useStored<T>(key: string, init: T): [T, (v: T) => void] {
  const v = useSyncExternalStore(subscribe, () => read(key, init), () => init);
  return [v, (x: T) => write(key, x)];
}
const NO_CONVS: Conv[] = [];
const newConv = (docId?: string, title = ""): Conv => ({ id: `c${Date.now()}`, title, scope: docId ? [docId] : [], msgs: [], updated: Date.now(), docId });

export default function Home() {
  const [picked, setView] = useState<View>("chat");
  const [admin, setAdmin] = useStored("aiocr.admin", false);
  const [theme, setTheme] = useStored<Theme>("aiocr.theme", "system");
  const [convs, setConvs] = useStored<Conv[]>("aiocr.convs.v2", NO_CONVS);
  const [draft, setDraft] = useState<Conv>(newConv);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [cmp, setCmp] = useState<string[]>([]);
  const [reader, setReader] = useState<{ id: string; page?: number; auto?: boolean; draft: Conv } | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [api, setApi] = useState<"wait" | "ok" | "down">("wait");
  const [docs, setDocs] = useState<Doc[]>([]);

  const refreshDocs = useCallback(() => { getDocs().then(setDocs).catch(() => {}); }, []);

  useEffect(() => {
    let stop = false;
    // Electron 이 API 서버를 막 띄운 직후라 준비될 때까지 1초마다 다시 본다(모델 로드 포함 최대 2분).
    const poll = async () => {
      try {
        await health();
        if (stop) return;
        setApi("ok");
        refreshDocs();
        getReport().then(setReport).catch(() => {});
      } catch {
        if (!stop) setTimeout(poll, 1000);
      }
    };
    poll();
    const t = setTimeout(() => setApi((s) => (s === "wait" ? "down" : s)), 120000);
    return () => { stop = true; clearTimeout(t); };
  }, [refreshDocs]);

  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") el.removeAttribute("data-theme"); else el.setAttribute("data-theme", theme);
  }, [theme]);

  const view: View = !admin && VIEWS[picked].admin ? "chat" : picked;  // 관리자 모드를 끄면 대화로
  const conv = convs.find((c) => c.id === activeId) ?? draft;
  const docConv = reader ? (convs.find((c) => c.docId === reader.id) ?? reader.draft) : null;  // 문서마다 대화 하나
  const saveConv = (c: Conv) => {
    if (!c.msgs.length) {
      if (c.id === draft.id) setDraft(c);
      else if (reader && c.id === reader.draft.id) setReader({ ...reader, draft: c });
      else setConvs(convs.map((x) => (x.id === c.id ? c : x)));
      return;
    }
    const rest = read<Conv[]>("aiocr.convs.v2", NO_CONVS).filter((x) => x.id !== c.id);
    setConvs([c, ...rest].sort((a, b) => b.updated - a.updated).slice(0, 50));
    if (c.id === draft.id) { setActiveId(c.id); setDraft(newConv()); }
  };
  const openDoc = (id: string, page?: number, auto?: boolean) => {
    const t = docs.find((x) => x.id === id)?.title ?? id;
    refreshDocs();
    setReader({ id, page, auto, draft: newConv(id, t) });
    setView("reader");
  };
  const startNew = () => { setView("chat"); setActiveId(null); setDraft(newConv()); };
  const v = VIEWS[view];
  const nav = (keys: View[]) => keys.map((k) => (
    <button key={k} className={k === view ? "on" : ""} onClick={() => setView(k)}>{VIEWS[k].label}</button>
  ));

  return (
    <div className="app">
      <nav className="side" aria-label="메뉴">
        <div className="brand">e-ocr{DEMO && <span className="pill warn" style={{ marginLeft: 6 }}>데모</span>}<small>스캔·PDF·웹·영상에 묻고 답 받기</small></div>
        <button className="newchat" onClick={startNew}><Icon name="plus" size={16} />새 대화</button>
        <button className={`navitem ${view === "docs" ? "on" : ""}`} onClick={() => setView("docs")}><Icon name="stack" size={16} />문서함<span className="count">{docs.length}</span></button>
        {convs.length > 0 && <div className="group">대화</div>}
        <div className="convs">
          {convs.map((c) => (
            <div key={c.id} className={`conv ${(view === "chat" && c.id === conv.id) || (view === "reader" && c.docId === reader?.id) ? "on" : ""}`}>
              <button className="ct" onClick={() => { if (c.docId) openDoc(c.docId); else { setView("chat"); setActiveId(c.id); } }} title={c.title}>
                {c.docId && <Icon name="file" size={13} />}{c.title || "새 대화"}</button>
              <button className="del" aria-label={`'${c.title}' 대화 지우기`}
                onClick={() => { setConvs(convs.filter((x) => x.id !== c.id)); if (c.id === activeId) startNew(); }}><Icon name="trash" size={14} /></button>
            </div>
          ))}
        </div>
        {admin && <><div className="group">관리자</div>{nav(["trace", "gate", "search", "report", "eval"])}</>}
        <label className="toggle"><input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} /> 관리자 모드</label>
        <div className="seg theme" role="group" aria-label="화면 밝기">
          {(["system", "light", "dark"] as Theme[]).map((t) => (
            <button key={t} className={t === theme ? "on" : ""} onClick={() => setTheme(t)}>{{ system: "자동", light: "밝게", dark: "어둡게" }[t]}</button>
          ))}
        </div>
      </nav>

      <main className={`main ${view === "chat" || view === "reader" ? "is-chat" : ""}`}>
        <div className="titlebar">
          <h1>{view === "chat" ? (conv.title || "새 대화") : view === "reader" ? (docs.find((x) => x.id === reader?.id)?.title ?? "문서") : v.label}</h1>
          {v.sub && <span className="sub">{v.sub}</span>}
          <span className="spacer" />
        </div>
        {api !== "ok" ? (
          <div className="content">
            <div className="panel" role="status">
              {api === "wait" ? <><span className="spin" />준비하는 중이에요 — 처음 켤 때는 모델을 불러오느라 1분쯤 걸릴 수 있어요.</> : (
                <div className="err">검색 서버에 연결하지 못했어요. 앱을 다시 켜 보세요. 계속되면 관리자에게 알려 주세요
                  (Qdrant <code>docker compose up -d</code> · Ollama 실행 여부).</div>
              )}
            </div>
          </div>
        ) : (
          <>
            {view === "chat" && <div className="chatwrap"><ChatView key={conv.id} conv={conv} onChange={saveConv} docs={docs} refreshDocs={refreshDocs}
              onManageDocs={() => setView("docs")} onOpenDoc={openDoc} /></div>}
            {view === "reader" && reader && docConv && <div className="chatwrap"><ReaderView key={reader.id} docId={reader.id} startPage={reader.page} autoSummary={reader.auto} docs={docs}
              conv={docConv} onChange={saveConv} refreshDocs={refreshDocs} onBack={() => setView("chat")} onManageDocs={() => setView("docs")} onOpenDoc={openDoc} /></div>}
            <div className="content" hidden={view === "chat" || view === "reader"}>
              <div hidden={view !== "docs"}><DocsView onChanged={refreshDocs} onOpen={openDoc} onCompare={(ids) => { setCmp(ids); setView("compare"); }} /></div>
              {view === "trace" && <TraceView />}
              {view === "gate" && <GateView />}
              <div hidden={view !== "search"}><CompareView /></div>
              {view === "compare" && cmp.length > 1 && <ReportPanel docs={cmp} onJump={(doc, page) => openDoc(doc, page)} onOpenOther={(doc, page) => openDoc(doc, page)} />}
              {view === "report" && report && <ReportView r={report} />}
              {view === "eval" && report && <EvalView r={report} />}
            </div>
          </>
        )}
      </main>

      <footer className="status">
        <span><i className={`dot ${api === "ok" ? "ok" : api === "down" ? "no" : ""}`} />{api === "ok" ? "준비됨" : api === "wait" ? "준비 중" : "연결 끊김"}</span>
        <span>문서 {docs.length}개</span>
        {admin && <span>임베딩 bge-m3 (ONNX) · reranker bge-reranker-v2-m3 · 생성 qwen2.5:7b / Claude · STT faster-whisper</span>}
      </footer>
    </div>
  );
}
