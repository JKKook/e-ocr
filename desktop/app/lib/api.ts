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

// ── 정적 데모(NEXT_PUBLIC_DEMO=1) — 서버 없이 미리 떠 둔 API 응답(/demo/<해시>.json)을 읽는다. 쓰기는 막는다.
// 해시 = 디코딩한 요청 경로의 FNV-1a 32bit(UTF-16 단위) — scripts/demo_export.py 의 demo_key 와 같아야 한다.
export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
export const DEMO_MSG = "데모에서는 미리 준비한 예시만 볼 수 있어요. 자료 추가·새 질문은 데스크톱 앱에서 돼요.";
export function demoKey(path: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < path.length; i++) { h ^= path.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h.toString(16).padStart(8, "0");
}
const demoPath = (p: string) => decodeURIComponent(p.replace(/\+/g, " "));
const demoFile = (p: string) => `/demo/${demoKey(demoPath(p))}.json`;
export const pageImage = (doc: string, page: number) =>
  DEMO ? `/demo/${demoKey(`/documents/${doc}/page/${page}.png`)}.png` : `${API}/documents/${encodeURIComponent(doc)}/page/${page}.png?t=${CFG.token}`;

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
const get = <T,>(path: string) => DEMO
  ? fetch(demoFile(path)).then((r) => (r.ok ? r.json() as Promise<T> : Promise.reject(new Error(DEMO_MSG))))
  : fetch(`${API}${path}`, { headers: H() }).then((r) => j<T>(r));
const send = <T,>(method: string, path: string, body?: unknown) => DEMO ? Promise.reject<T>(new Error(DEMO_MSG)) :
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
  if (DEMO) return Promise.reject<Job>(new Error(DEMO_MSG));
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
/** 데모: 녹화한 /chat 이벤트를 다시 흘린다(토큰은 잘게 나눠 타이핑처럼). 녹화 키 = llm·범위·질문. */
export const demoChatKey = (q: string, llm: string, docs: string[]) => `chat|${llm}|${[...docs].sort().join(",")}|${q.trim()}`;
async function demoChat(q: string, llm: string, on: ChatEvents, docs: string[]) {
  const r = await fetch(`/demo/${demoKey(demoChatKey(q, llm, docs))}.json`);
  if (!r.ok) return on.error(`${DEMO_MSG} 첫 화면의 예시 질문을 눌러 보세요.`);
  const evs: [keyof ChatEvents, unknown][] = await r.json();
  const wait = (ms: number) => new Promise((res) => setTimeout(res, ms));
  for (const [ev, data] of evs) {
    if (ev === "token" || ev === "replace") continue;  // 녹화한 토큰 대신 최종 답을 다시 쪼개 흘린다
    if (ev === "done") {
      const text = (data as Done).answer;
      for (let i = 0; i < text.length; i += 6) { on.token(text.slice(i, i + 6)); await wait(12); }
      on.replace(text);
    } else await wait(ev === "hits" ? 500 : 0);
    (on[ev] as (x: unknown) => void)(data);
  }
}

/** 데모에서 눌러 볼 수 있는 녹화된 질문(첫 화면·검색 비교). */
export const getDemoPresets = () => get<string[]>("/__demo/presets");

export async function chat(q: string, history: { role: string; text: string }[], llm: "local" | "claude", on: ChatEvents, docs: string[] = []) {
  if (DEMO) return demoChat(q, llm, on, docs);
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
export const rawUrl = (doc: string) => DEMO ? `/demo/${demoKey(`/documents/${doc}/raw`)}.pdf` : `${API}/documents/${encodeURIComponent(doc)}/raw?t=${CFG.token}`;

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
