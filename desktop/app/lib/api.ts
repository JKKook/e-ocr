// FastAPI(src/aiocr/api.py) 호출. Electron 이 실행마다 빈 포트·새 토큰으로 API 를 띄우고 #api=…&t=… 로 넘긴다.
// hash 는 서버로 가지 않는다. 새로고침에도 남도록 sessionStorage 에 옮겨 두고 주소창에서는 지운다.
function boot(): { api: string; token: string } {
  const fallback = { api: process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8765", token: "" };
  if (typeof window === "undefined") return fallback;
  try {
    const h = new URLSearchParams(window.location.hash.slice(1));
    if (h.get("api")) {
      sessionStorage.setItem("aiocr", JSON.stringify({ api: h.get("api"), token: h.get("t") ?? "" }));
      history.replaceState(null, "", window.location.pathname);
    }
    const s = sessionStorage.getItem("aiocr");
    return s ? JSON.parse(s) : fallback;
  } catch {
    return fallback;
  }
}
const CFG = boot();
export const API = CFG.api;
const H = (extra: Record<string, string> = {}) => ({ "x-aiocr-token": CFG.token, ...extra });
export const pageImage = (doc: string, page: number) => `${API}/documents/${encodeURIComponent(doc)}/page/${page}.png?t=${CFG.token}`;

export type Mode = "dense" | "sparse" | "hybrid" | "hybrid+rerank";
export const MODES: Mode[] = ["dense", "sparse", "hybrid", "hybrid+rerank"];
export const MODE_LABEL: Record<Mode, string> = {
  dense: "dense", sparse: "sparse", hybrid: "hybrid (RRF)", "hybrid+rerank": "hybrid + rerank",
};
export const COLLECTIONS = ["vlm_ctx", "vlm_base", "rapidocr_ctx", "rapidocr_base"] as const;
export const COLLECTION_LABEL: Record<string, string> = {
  vlm_ctx: "VLM OCR · contextual", vlm_base: "VLM OCR · 기본",
  rapidocr_ctx: "RapidOCR · contextual", rapidocr_base: "RapidOCR · 기본",
};

export type Hit = { id: string; doc: string; title: string; page: number; via: string; t0?: number | null; context?: string; text: string; score: number };
export type LlmAnswer = { text: string; sec: number; usd: number; in_tok?: number; out_tok?: number;
  judge?: { correct: 0 | 1 | 2; grounded: boolean; why: string } };
export type Metric = { collection: string; mode: Mode; n: number; "hit@1": number; "recall@5": number; "mrr@10": number;
  sec_per_q: number; by_src: Record<string, { "recall@5": number }> };
export type Report = {
  retrieval: Record<string, Metric>;
  ocr: Record<string, Record<string, { cer: number; sec_per_page: number }>>;
  answers: { qid: string; q: string; gold: string; ctx_ids: string[]; local: LlmAnswer; claude: LlmAnswer }[];
  scorecard: Scorecard;
};
export type ScoreItem = { id: string; term: string; def: string; score: { impl: number; measure: number; limits: number }; total: number;
  did: string; gap: string; next: { do: string; gain: number; expects: string | null } | null;
  evidence: { label: string; path: string; now: unknown; seen?: unknown }[]; stale: string[] };
export type Scorecard = { name: string; rubric: string; how_to_update: string; items: ScoreItem[]; total: number; max: number;
  history: { when: string; total: number; scores: Record<string, number>; note: string }[] };
export type Confidence = "high" | "low" | "none";
export type Done = { trace_id?: number; confidence: Confidence; issues: Record<string, unknown>; retried: boolean; llm: string;
  answer: string; standalone: string; stages: Record<string, number>; usd: number; top_score: number };
export type Doc = { id: string; title: string; group: string; kind: string; url?: string; path?: string; type: string;
  pages: number; chunks: number; ocr_pages: number; deletable: boolean; added: number; duration?: number };
export type Job = { id: string; label: string; state: "queued" | "running" | "ok" | "error"; stage: string;
  log: { t: number; stage: string; msg: string }[]; error?: string; result?: { id: string; title: string; chunks: Record<string, number>; existing?: boolean } };
export type TraceRow = Done & { id: number; ts: number; q: string; collection: string; hits: string[]; in_tok?: number; out_tok?: number; feedback: number | null };
export type TraceStats = { n: number; stages: Record<string, { p50: number | null; p95: number | null; n: number }>;
  refused: number; low: number; flagged: number; retried: number; usd: number; feedback: { up: number; down: number } };
export type Gate = { when: string; baseline_when: string; pass: boolean; config: Record<string, string | number>;
  checks: { metric: string; baseline: number; now: number; delta: number; tol: number; pass: boolean }[]; n: Record<string, number> } | null;

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* 본문 없음 */ }
    throw new Error(msg);
  }
  return r.json();
}
const get = <T,>(path: string) => fetch(`${API}${path}`, { headers: H() }).then((r) => j<T>(r));
const send = <T,>(method: string, path: string, body?: unknown) =>
  fetch(`${API}${path}`, { method, headers: H({ "content-type": "application/json" }), body: body ? JSON.stringify(body) : undefined })
    .then((r) => j<T>(r));

export const health = () => get<{ collections: string[] }>("/health");
export const getReport = () => get<Report>("/report");
export const getDocs = () => get<Doc[]>("/documents");
export const getJob = (id: string) => get<Job>(`/jobs/${id}`);
export const addUrl = (url: string) => send<Job>("POST", "/documents/url", { url });
export const deleteDoc = (id: string) => send<{ ok: boolean }>("DELETE", `/documents/${encodeURIComponent(id)}`);
export const pageText = (doc: string, page: number) => get<{ text: string; via: string; image: boolean; t0?: number | null }>(`/documents/${encodeURIComponent(doc)}/page/${page}`);
export const feedback = (trace_id: number, value: 1 | -1) => send<{ ok: boolean }>("POST", "/feedback", { trace_id, value });
export const getTraces = () => get<TraceRow[]>("/traces?limit=200");
export const getTraceStats = () => get<TraceStats>("/traces/stats");
export const getGate = () => get<Gate>("/gate");
export const search = (q: string, collection: string, mode: Mode, k = 5) =>
  get<{ sec: number; hits: Hit[] }>(`/search?${new URLSearchParams({ q, collection, mode, k: String(k) })}`);

export function uploadFile(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`${API}/documents/upload`, { method: "POST", headers: H(), body: fd }).then((r) => j<Job>(r));
}

export type ChatEvents = {
  hits: (d: { hits: Hit[]; confidence: Confidence; standalone: string }) => void;
  token: (t: string) => void;
  replace: (t: string) => void;
  done: (d: Done) => void;
  error: (msg: string) => void;
};

/** /chat SSE 를 fetch 스트림으로 읽는다(EventSource 는 POST·헤더를 못 쓴다). */
export async function chat(q: string, history: { role: string; text: string }[], llm: "local" | "claude", on: ChatEvents, docs: string[] = []) {
  const r = await fetch(`${API}/chat`, { method: "POST", headers: H({ "content-type": "application/json" }), body: JSON.stringify({ q, history, llm, docs }) });
  if (!r.ok || !r.body) return on.error(`${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const ev = /^event: (.+)$/m.exec(block)?.[1] as keyof ChatEvents | undefined;
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (ev && data !== undefined) (on[ev] as (x: unknown) => void)(JSON.parse(data));
    }
  }
}

export type Part = { s: number; k: string; t: string; proper?: string[]; garbled?: { orig: string; guess: string }[] };
export type DocPage = { page: number; via: string; t0?: number | null; text: string; image: boolean; parts?: Part[] | null };
export type PolicyKind = { id: string; label: string; translate: string; rule: string; mark?: string[] | string };
export type Policy = { name: string; version: number; decided: string; line_kinds: PolicyKind[]; span_kinds: PolicyKind[] };
export const getPolicy = () => get<Policy>("/policy/transcript");
export const setSegments = (doc: string, starts: number[], kind: string) =>
  send<Job>("POST", `/documents/${encodeURIComponent(doc)}/segments`, { starts, kind });
export type DocDetail = Omit<Doc, "pages"> & { pages: DocPage[] };
export const getDoc = (id: string) => get<DocDetail>(`/documents/${encodeURIComponent(id)}`);
export const rawUrl = (doc: string) => `${API}/documents/${encodeURIComponent(doc)}/raw?t=${CFG.token}`;

// ── 보고서 모드 ──
export type KW = { term: string; score?: number; z?: number; count: number; pages: number[]; chunk: string | null };
export type Topic = { topic: number; size: number; keywords: string[]; pages: number[]; rep: string; rep_text: string; name?: string; desc?: string; docs: Record<string, number> };
export type Topics = { k: number; silhouette: number | null; assign: Record<string, number>; topics: Topic[] };
export type DocReport = {
  docs: string[]; titles: Record<string, string>; compare: boolean; llm: string; made: number; sec?: number; stale?: boolean; done: string[];
  chunks: Record<string, { text: string; page: number; doc: string }>;
  sections: {
    keywords?: KW[]; contrast?: Record<string, KW[]>; shared?: string[]; topics?: Topics;
    map?: { points: { id: string; doc: string; page: number; xy: [number, number] }[] };
    stats?: Record<string, unknown> & { pages: Record<string, number> };
    summary?: { gist?: string; points?: string[]; cites?: { n: number; id: string; page: number }[]; issues?: Record<string, unknown>;
      rows?: { doc: string; gist: string; points: string[] }[] };
    pairs?: { a: string; b: string; diff: string; page: number | null; chunk: string | null }[];
    structure?: { title: string; page: number; chunk: string | null; line?: string }[];
    numbers?: { value: string; meaning: string; text: string; page: number; chunk: string }[];
    glossary?: { term: string; def: string; page: number | null; chunk: string | null }[];
    questions?: string[];
  };
};
export const getSavedReport = (docs: string[]) => get<DocReport | null>(`/reports?docs=${encodeURIComponent(docs.join(","))}`);
export const startReport = (docs: string[], llm: "local" | "claude") => send<{ id: string }>("POST", "/reports", { docs, llm });
export const reportJob = (id: string) => get<{ state: string; report: DocReport | null; error?: string }>(`/reports/job/${id}`);
export const reTopic = (docs: string[], k: number) => send<Topics>("POST", "/reports/topics", { docs, k });
export type NN = { id: string; doc: string; title: string; page: number; sim: number; text: string; shared: string[] };
export type NNRes = { terms: { term: string; w: number }[]; items: NN[] };
export const getNeighbors = (id: string, k = 8) => get<NNRes>(`/vectors/neighbors?id=${encodeURIComponent(id)}&k=${k}`);
