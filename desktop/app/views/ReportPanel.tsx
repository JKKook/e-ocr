"use client";
import { useEffect, useRef, useState } from "react";
import { getNeighbors, getSavedReport, reportJob, reTopic, startReport, type DocReport, type KW, type NNRes, type Topics } from "../lib/api";
import { Icon } from "./icons";

export type Jump = (doc: string, page: number, text?: string, term?: string) => void;
type Props = { docs: string[]; onJump: Jump; onAsk?: (q: string) => void; searchIds?: string[]; onOpenOther?: Jump };

const ORDER_ONE = ["summary", "keywords", "contrast", "pairs", "topics", "map", "structure", "numbers", "glossary", "questions", "stats"];
const ORDER_CMP = ["summary", "shared", "contrast", "pairs", "topics", "map", "stats"];
const NAME: Record<string, string> = {
  summary: "요지·요약", keywords: "핵심 키워드", contrast: "대비 키워드", shared: "공통 키워드", pairs: "대립 개념", topics: "topic",
  map: "벡터 지도", structure: "문서 구조", numbers: "수치 모음", glossary: "용어집", questions: "읽기 질문", stats: "색인 통계",
};
const tc = (i: number) => `var(--t${i % 8})`;  // topic·문서 색(8개, 라이트·다크 토큰)

/** 보고서 모드 — 통계 절이 먼저, LLM 절은 끝나는 대로. 항목을 누르면 원문 그 쪽으로 이동·강조. */
export function ReportPanel({ docs, onJump, onAsk, searchIds = [], onOpenOther }: Props) {
  const [r, setR] = useState<DocReport | null | undefined>(undefined);
  const [job, setJob] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [llm, setLlm] = useState<"local" | "claude">("local");
  const [tp, setTp] = useState<Topics | null>(null);
  const key = docs.join(",");

  useEffect(() => {
    let live = true;
    getSavedReport(key.split(",")).then((x) => { if (live) { setR(x); setTp(null); } }).catch((e) => setErr(String(e)));
    return () => { live = false; };
  }, [key]);

  useEffect(() => {
    if (!job) return;
    const t = setInterval(async () => {
      try {
        const j = await reportJob(job);
        if (j.report) setR(j.report);
        if (j.state === "ok" || j.state === "error") { setJob(null); if (j.error) setErr(j.error); }
      } catch (e) { setErr(String(e)); setJob(null); }
    }, 1000);
    return () => clearInterval(t);
  }, [job]);

  async function make() {
    setErr(""); setTp(null);
    try { setJob((await startReport(key.split(","), llm)).id); setR((x) => (x ? { ...x, done: [] } : x)); } catch (e) { setErr(String(e)); }
  }

  const modelSeg = (
    <div className="seg" role="group" aria-label="보고서를 쓰는 모델">
      <button className={llm === "local" ? "on" : ""} onClick={() => setLlm("local")}>내 컴퓨터</button>
      <button className={llm === "claude" ? "on" : ""} onClick={() => setLlm("claude")}>Claude</button>
    </div>
  );

  if (r === undefined) return <div className="muted"><span className="spin" />보고서를 찾는 중…</div>;
  if (!r && !job) {
    return (
      <div className="rp-empty">
        <Icon name="sum" size={28} />
        <h3>{docs.length > 1 ? `문서 ${docs.length}개 비교 보고서` : "이 문서의 보고서"}</h3>
        <p className="muted">{(docs.length > 1 ? ORDER_CMP : ORDER_ONE).map((s) => NAME[s]).join(" · ")}</p>
        <p className="muted small">통계 절(키워드·topic·벡터 지도)은 몇 초, 요약·용어집 같은 글은 내 컴퓨터 기준 1분 안팎 걸려요. 만든 보고서는 저장돼 다음엔 바로 열려요.</p>
        <div className="row" style={{ justifyContent: "center" }}>{modelSeg}<button className="btn primary" onClick={make}><Icon name="sum" size={15} />보고서 만들기</button></div>
        {err && <div className="err">{err}</div>}
      </div>
    );
  }

  const S = r?.sections ?? {};
  const order = r?.compare || docs.length > 1 ? ORDER_CMP : ORDER_ONE;
  const topics = tp ?? S.topics;
  const ch = r?.chunks ?? {};  // 11-10 이전 저장본에는 chunks 가 없다
  const text = (id: string | null | undefined) => (id && ch[id]?.text) || undefined;
  const docOf = (id: string | null | undefined) => (id && ch[id]?.doc) || docs[0];
  const pg = (d: string, p: number | null | undefined, id?: string | null, term?: string) =>
    p ? <button key={`${d}-${p}-${term ?? ""}`} className="pg" onClick={() => onJump(d, p, text(id ?? null), term)}>{p}쪽</button> : null;
  const pending = (name: string) => !!job && !(r?.done ?? []).includes(name);
  const multi = docs.length > 1;

  return (
    <div className="report">
      <div className="rp-head">
        <div style={{ minWidth: 0 }}>
          <b>{multi ? `비교 보고서 — ${docs.map((d) => r?.titles[d] ?? d).join(" · ")}` : "보고서"}</b>
          <div className="muted small">
            {job ? `만드는 중 — ${(r?.done ?? []).length}/${order.length}절` : r ? `${new Date(r.made * 1000).toLocaleString("ko-KR")} · ${r.llm === "claude" ? "Claude" : "내 컴퓨터"} · ${r.sec ?? "–"}초` : ""}
            {r?.stale && !job && <span className="pill warn" style={{ marginLeft: 6 }}>문서가 바뀜 — 오래된 보고서</span>}
          </div>
        </div>
        <div className="row" style={{ flexWrap: "nowrap" }}>{modelSeg}<button className="btn" disabled={!!job} onClick={make}>{job ? <><span className="spin" />만드는 중</> : "다시 만들기"}</button></div>
      </div>
      {err && <div className="note warn">{err}</div>}
      <nav className="rp-toc" aria-label="보고서 절">
        {order.map((s, i) => <a key={s} href={`#rp-${s}`} className={pending(s) ? "wait" : ""}>{String(i + 1).padStart(2, "0")} {NAME[s]}{pending(s) && <span className="spin" />}</a>)}
      </nav>

      {order.map((s, i) => (
        <section key={s} id={`rp-${s}`} className="rp-sec">
          <h3><span className="stage-no">{String(i + 1).padStart(2, "0")}</span>{NAME[s]}</h3>
          {!S[s as keyof typeof S] && pending(s) ? <div className="skel"><span className="spin" />{["summary", "pairs", "structure", "numbers", "glossary", "questions"].includes(s) ? "글을 쓰는 중…" : "계산 중…"}</div>
            : s === "summary" ? <Summary r={r!} pg={pg} multi={multi} />
            : s === "keywords" ? <Bars rows={S.keywords ?? []} val={(k) => k.score ?? 0} fmt={(k) => `${k.count}회`} pg={(k) => k.pages.slice(0, 6).map((p) => pg(docs[0], p, k.chunk, k.term))}
                note="이 문서에서 많이 나오면서 다른 문서에는 드문 말(c-TF-IDF · kiwi 명사·영문 고유명)" />
            : s === "contrast" ? (
              <div className={multi ? "cols" : ""}>
                {docs.map((d, di) => (
                  <div key={d}>
                    {multi && <div className="dlabel" style={{ color: tc(di) }}>■ {r?.titles[d]}</div>}
                    <Bars rows={S.contrast?.[d] ?? []} val={(k) => k.z ?? 0} fmt={(k) => `z ${k.z}`} color={multi ? tc(di) : undefined}
                      pg={(k) => k.pages.slice(0, 4).map((p) => pg(d, p, k.chunk, k.term))}
                      note={multi ? "다른 고른 문서보다 이 문서가 더 쓰는 말" : "말뭉치 나머지 문서보다 이 문서가 더 쓰는 말 — 로그오즈 비율 z 점수(사전 포함)"} />
                  </div>
                ))}
              </div>)
            : s === "shared" ? (
              <div className="chips">{(S.shared ?? []).length ? S.shared!.map((t) => <span key={t} className="chip">{t}</span>) : <span className="muted">고른 문서 모두의 상위 키워드에 함께 든 말이 없어요.</span>}</div>)
            : s === "pairs" ? (
              <div className="pairs">
                {(S.pairs ?? []).length === 0 && <span className="muted">뽑힌 대립 개념이 없어요.</span>}
                {(S.pairs ?? []).map((p, j) => (
                  <div key={j} className="pair"><div className="ab"><b>{p.a}</b><span className="vs">↔</span><b>{p.b}</b></div><p>{p.diff}</p>{pg(docOf(p.chunk), p.page, p.chunk)}</div>
                ))}
              </div>)
            : s === "topics" && topics ? <TopicSec t={topics} docs={docs} titles={r!.titles} pg={pg} onK={async (k) => setTp(await reTopic(docs, k))} />
            : s === "map" && S.map ? <MapSec r={r!} t={topics ?? null} docs={docs} searchIds={searchIds} onJump={onJump} onOpenOther={onOpenOther} />
            : s === "structure" ? (
              <ol className="toc2">{(S.structure ?? []).map((h, j) => <li key={j}><button className="linkish" onClick={() => onJump(docs[0], h.page, text(h.chunk), h.title)}><b>{h.title}</b></button>{h.line && <span className="muted"> — {h.line}</span>} <span className="muted small">{h.page}쪽</span></li>)}</ol>)
            : s === "numbers" ? (
              <div className="tablewrap"><table><thead><tr><th>수치</th><th>뜻</th><th>원문</th><th>쪽</th></tr></thead><tbody>
                {(S.numbers ?? []).map((n, j) => <tr key={j}><td className="num"><b>{n.value}</b></td><td className="t">{n.meaning}</td><td className="t muted small">{n.text.slice(0, 120)}</td><td>{pg(docs[0], n.page, n.chunk, n.value)}</td></tr>)}
              </tbody></table>{(S.numbers ?? []).length === 0 && <span className="muted">원문에서 단위가 붙은 수치를 찾지 못했어요.</span>}</div>)
            : s === "glossary" ? (
              <dl className="gloss">{(S.glossary ?? []).map((g) => <div key={g.term}><dt>{g.term}</dt><dd>{g.def} {pg(docOf(g.chunk), g.page, g.chunk, g.term)}</dd></div>)}</dl>)
            : s === "questions" ? (
              <div className="qs">{(S.questions ?? []).map((q) => <button key={q} className="sample" onClick={() => onAsk?.(q)} disabled={!onAsk}><Icon name="chat" size={14} />{q}</button>)}</div>)
            : s === "stats" && S.stats ? <Stats s={S.stats} titles={r!.titles} /> : null}
        </section>
      ))}
    </div>
  );
}

function Summary({ r, pg, multi }: { r: DocReport; pg: (d: string, p: number, id?: string | null) => React.ReactNode; multi: boolean }) {
  const s = r.sections.summary!;
  if (multi) {
    return (
      <div className="tablewrap"><table><thead><tr><th>문서</th><th>요지</th><th>핵심</th></tr></thead><tbody>
        {(s.rows ?? []).map((x, i) => <tr key={x.doc}><td className="t"><b style={{ color: `var(--t${i})` }}>■</b> {r.titles[x.doc]}</td><td className="t">{x.gist}</td><td className="t"><ul>{x.points.map((p) => <li key={p}>{p}</li>)}</ul></td></tr>)}
      </tbody></table></div>
    );
  }
  const cite = (line: string) => line.split(/(\[\d+\])/g).map((part, i) => {
    const n = /^\[(\d+)\]$/.exec(part)?.[1];
    const c = n ? s.cites?.find((x) => x.n === Number(n)) : undefined;
    return c ? <span key={i}>{pg(r.docs[0], c.page, c.id)}</span> : <span key={i}>{part}</span>;
  });
  return (
    <>
      <p className="gist">{s.gist}</p>
      <ul className="pts">{(s.points ?? []).map((p, i) => <li key={i}>{cite(p)}</li>)}</ul>
      {s.issues && Object.keys(s.issues).length > 0 && <div className="note warn">요약 속 일부 내용(특히 숫자)을 근거에서 확인하지 못했어요.</div>}
    </>
  );
}

function Bars({ rows, val, fmt, pg, note, color }: { rows: KW[]; val: (k: KW) => number; fmt: (k: KW) => string; pg: (k: KW) => React.ReactNode; note: string; color?: string }) {
  const max = Math.max(1e-9, ...rows.map(val));
  return (
    <>
      <div className="bars">
        {rows.map((k) => (
          <div key={k.term} className="barrow">
            <span className="term">{k.term}</span>
            <span className="bar"><i style={{ width: `${(val(k) / max) * 100}%`, background: color }} /></span>
            <span className="muted mono small">{fmt(k)}</span>
            <span className="pgs">{pg(k)}</span>
          </div>
        ))}
      </div>
      <p className="muted small">{note}</p>
    </>
  );
}

function TopicSec({ t, docs, titles, pg, onK }: { t: Topics; docs: string[]; titles: Record<string, string>; pg: (d: string, p: number, id?: string | null, term?: string) => React.ReactNode; onK: (k: number) => void }) {
  return (
    <>
      <div className="row">
        <label className="muted small" htmlFor="tk">topic 수 {t.k}</label>
        <input id="tk" type="range" min={1} max={8} value={t.k} onChange={(e) => onK(Number(e.target.value))} style={{ accentColor: "var(--accent)" }} />
        <span className="muted small">KMeans(PCA 10차원) · 실루엣 {t.silhouette ?? "–"} — 슬라이더로 바꾸면 이름은 키워드로 표시돼요(다시 만들기 때 이름 생성)</span>
      </div>
      <div className="topics">
        {t.topics.map((x) => (
          <div key={x.topic} className="tcard" style={{ borderTopColor: tc(x.topic) }}>
            <div className="row"><b style={{ color: tc(x.topic) }}>●</b><b>{x.name ?? x.keywords.slice(0, 3).join(" · ")}</b>{!x.name && <span className="spin" aria-label="이름 붙이는 중" />}<span className="muted small">청크 {x.size}</span></div>
            {x.desc && <p>{x.desc}</p>}
            <div className="chips">{x.keywords.map((k) => <span key={k} className="chip">{k}</span>)}</div>
            {docs.length > 1 && <div className="muted small">{Object.entries(x.docs).map(([d, n]) => `${titles[d] ?? d} ${n}`).join(" · ")}</div>}
            <div className="pgs">{docs.length === 1 && x.pages.slice(0, 8).map((p) => pg(docs[0], p, x.rep, undefined))}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function MapSec({ r, t, docs, searchIds, onJump, onOpenOther }: { r: DocReport; t: Topics | null; docs: string[]; searchIds: string[]; onJump: Jump; onOpenOther?: Jump }) {
  const pts = r.sections.map!.points;
  const W = 640, H = 400, pad = 10;
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });  // 화면 = (점 - x) * k
  const [hover, setHover] = useState<string | null>(null);
  const [pin, setPin] = useState<string | null>(null);
  const [nn, setNn] = useState<Record<string, NNRes>>({});
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ sx: number; sy: number; x: number; y: number; moved: boolean } | null>(null);
  const [grab, setGrab] = useState(false);
  const focus = pin ?? hover;
  const byId = new Map(pts.map((p) => [p.id, p]));
  const P = (xy: [number, number]) => [pad + xy[0] * (W - 2 * pad), pad + xy[1] * (H - 2 * pad)] as const;
  const S = (xy: [number, number]) => { const [x, y] = P(xy); return [(x - view.x) * view.k, (y - view.y) * view.k] as const; };
  const color = (p: (typeof pts)[number]) => (docs.length > 1 ? tc(docs.indexOf(p.doc)) : tc(t?.assign[p.id] ?? 0));
  const mine = (p: (typeof pts)[number]) => docs.includes(p.doc);

  useEffect(() => {  // 이웃은 원래 1024차원 코사인으로 — 한 번 받은 것은 다시 묻지 않는다
    if (!focus || nn[focus]) return;
    const id = focus;
    const tm = setTimeout(() => getNeighbors(id).then((x) => setNn((m) => ({ ...m, [id]: x }))).catch(() => {}), 80);
    return () => clearTimeout(tm);
  }, [focus, nn]);

  useEffect(() => {  // 휠 확대(커서 중심) — React onWheel 은 passive 라 기본 스크롤을 못 막아 직접 붙인다
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const b = el.getBoundingClientRect();
      const mx = ((e.clientX - b.left) / b.width) * W, my = ((e.clientY - b.top) / b.height) * H;
      setView((v) => {
        const k = Math.min(16, Math.max(1, v.k * Math.exp(-e.deltaY * 0.0018)));
        const wx = v.x + mx / v.k, wy = v.y + my / v.k;  // 커서 아래 점이 그대로 있게
        return k === 1 ? { x: 0, y: 0, k } : { x: wx - mx / k, y: wy - my / k, k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const zoom = (f: number) => setView((v) => {
    const k = Math.min(16, Math.max(1, v.k * f));
    const cx = v.x + W / 2 / v.k, cy = v.y + H / 2 / v.k;
    return k === 1 ? { x: 0, y: 0, k } : { x: cx - W / 2 / k, y: cy - H / 2 / k, k };
  });
  const down = (e: React.PointerEvent) => { drag.current = { sx: e.clientX, sy: e.clientY, x: view.x, y: view.y, moved: false }; };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d || !svg.current) return;
    const b = svg.current.getBoundingClientRect();
    const dx = ((e.clientX - d.sx) / b.width) * W / view.k, dy = ((e.clientY - d.sy) / b.height) * H / view.k;
    if (Math.abs(dx) + Math.abs(dy) > 1 / view.k && !d.moved) { d.moved = true; setGrab(true); }
    if (d.moved && view.k > 1) setView((v) => ({ ...v, x: d.x - dx, y: d.y - dy }));
  };
  const up = () => { setGrab(false); setTimeout(() => { drag.current = null; }, 0); };

  const f = focus ? byId.get(focus) : undefined;
  const near = focus ? nn[focus]?.items ?? [] : [];
  const terms = focus ? nn[focus]?.terms ?? [] : [];
  const nearIds = new Set(near.map((x) => x.id));
  const rr = (base: number) => base * Math.min(1.6, 0.85 + view.k * 0.15);  // 확대해도 점은 거의 같은 크기
  const open = (doc: string, page: number, text?: string) => (docs.includes(doc) ? onJump(doc, page, text) : onOpenOther?.(doc, page, text));

  return (
    <div className="vmapwrap">
      <figure className="vmap">
        <div className="vtools" role="group" aria-label="지도 확대">
          <button className="ibtn" onClick={() => zoom(1.5)} aria-label="확대">＋</button>
          <button className="ibtn" onClick={() => zoom(1 / 1.5)} aria-label="축소">−</button>
          <button className="ibtn" onClick={() => setView({ x: 0, y: 0, k: 1 })}>맞춤</button>
          <span className="muted mono small">{view.k.toFixed(1)}×</span>
        </div>
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`청크 ${pts.length}개의 2D 지도. 점에 마우스를 올리면 의미가 가까운 청크를 잇는다`}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={() => { up(); setHover(null); }}
          style={{ cursor: view.k > 1 ? (grab ? "grabbing" : "grab") : "default" }}>
          {pts.filter((p) => !mine(p)).map((p) => {
            const [x, y] = S(p.xy);
            return <circle key={p.id} cx={x} cy={y} r={rr(nearIds.has(p.id) ? 4 : 2.3)} fill={nearIds.has(p.id) ? "var(--ink)" : "var(--muted)"} opacity={focus && !nearIds.has(p.id) && p.id !== focus ? 0.15 : nearIds.has(p.id) ? 0.9 : 0.32}
              onMouseEnter={() => setHover(p.id)} onClick={() => { const moved = drag.current?.moved; if (!moved) setPin((q) => (q === p.id ? null : p.id)); }} />;
          })}
          {f && near.map((x) => {
            const q = byId.get(x.id);
            if (!q) return null;
            const [x1, y1] = S(f.xy), [x2, y2] = S(q.xy);
            return <line key={`l-${x.id}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--accent)" strokeWidth={1 + (x.sim - 0.4) * 4} opacity={Math.max(0.25, (x.sim - 0.35) * 1.6)} pointerEvents="none" />;
          })}
          {pts.filter(mine).map((p) => {
            const [x, y] = S(p.xy);
            const on = p.id === focus || nearIds.has(p.id);
            return <circle key={p.id} cx={x} cy={y} r={rr(on ? 6 : 4.2)} fill={color(p)} stroke={on ? "var(--ink)" : "var(--surface)"} strokeWidth={on ? 1.6 : 1}
              opacity={focus && !on ? 0.45 : 1}
              onMouseEnter={() => setHover(p.id)} onClick={() => { const moved = drag.current?.moved; if (!moved) setPin((q) => (q === p.id ? null : p.id)); }} style={{ cursor: "pointer" }} />;
          })}
          {pts.filter((p) => searchIds.includes(p.id)).map((p) => { const [x, y] = S(p.xy); return <circle key={`s-${p.id}`} cx={x} cy={y} r={rr(9)} fill="none" stroke="var(--accent)" strokeWidth={2.2} pointerEvents="none" />; })}
          {f && (() => { const [x, y] = S(f.xy); return <circle cx={x} cy={y} r={rr(10)} fill="none" stroke="var(--ink)" strokeWidth={2} strokeDasharray={pin ? "0" : "3 3"} pointerEvents="none" />; })()}
        </svg>
        <figcaption>
          회색 = 말뭉치의 다른 청크({pts.length - pts.filter(mine).length}개) · 색 = {docs.length > 1 ? "고른 문서별" : "이 문서의 topic별"} 청크({pts.filter(mine).length}개)
          {searchIds.length > 0 && " · 주황 고리 = 방금 대화에서 검색된 근거"} · 점에 올리면 의미가 가까운 청크 8개를 주황 선으로(굵을수록 가까움), 누르면 고정 · 휠로 확대, 끌어서 이동.
          지도 = bge-m3 dense → PCA 50 → t-SNE 2D, 연관성 = 원래 1024차원 코사인 유사도(2D 거리는 왜곡될 수 있음).
        </figcaption>
      </figure>
      <aside className="nnpanel" aria-live="polite">
        {!f ? <p className="muted small">점에 마우스를 올리면 그 청크와 의미가 가장 가까운 청크들이 여기에 나와요. 다른 문서의 청크도 포함합니다.</p> : (
          <>
            <div className="nnhead">
              <span className="dot2" style={{ background: mine(f) ? color(f) : "var(--muted)" }} />
              <div style={{ minWidth: 0 }}>
                <b>{r.titles[f.doc] ?? near[0]?.title ?? f.doc}</b> <span className="muted small">{f.page}쪽{pin ? " · 고정됨" : ""}</span>
                <div className="muted small clip3">{r.chunks?.[f.id]?.text.slice(0, 200) ?? ""}</div>
              </div>
            </div>
            <div className="row" style={{ margin: "6px 0 8px" }}>
              {(docs.includes(f.doc) || onOpenOther) && <button className="btn" onClick={() => open(f.doc, f.page, r.chunks?.[f.id]?.text)}><Icon name="file" size={14} />원문 보기</button>}
              {pin && <button className="btn" onClick={() => setPin(null)}>고정 풀기</button>}
            </div>
            {terms.length > 0 && (
              <div className="sterms" aria-label="sparse 키워드">
                <div className="muted small">이 청크의 sparse 키워드(bge-m3 가중치)</div>
                <div className="chips">{terms.map((x) => <span key={x.term} className="chip w" style={{ ["--w" as string]: Math.min(1, x.w / (terms[0]?.w || 1)) }} title={`가중치 ${x.w}`}>{x.term}</span>)}</div>
              </div>
            )}
            <div className="muted small" style={{ marginBottom: 4 }}>의미가 가까운 청크 {near.length ? `${near.length}개` : "…"} · 칩 = 두 청크가 함께 무게를 둔 낱말</div>
            {!nn[f.id] && <span className="spin" />}
            {near.map((x) => (
              <button key={x.id} className={`nnitem ${docs.includes(x.doc) ? "" : "other"}`} onMouseEnter={() => setHover(f.id)}
                onClick={() => open(x.doc, x.page, x.text)} disabled={!docs.includes(x.doc) && !onOpenOther}>
                <span className="sim"><i style={{ width: `${Math.max(0, (x.sim - 0.3) / 0.7) * 100}%` }} /></span>
                <span className="mono small">{x.sim.toFixed(2)}</span>
                <span className="t"><b>{x.title}</b> <span className="muted">{x.page}쪽{docs.includes(x.doc) ? "" : " · 다른 문서"}</span><br /><span className="muted small">{x.text.replace(/\s+/g, " ").slice(0, 110)}</span>
                  {x.shared.length > 0 && <span className="chips" style={{ marginTop: 3 }}>{x.shared.map((w) => <span key={w} className="chip sm">{w}</span>)}</span>}</span>
              </button>
            ))}
          </>
        )}
      </aside>
    </div>
  );
}

function Stats({ s, titles }: { s: NonNullable<DocReport["sections"]["stats"]>; titles: Record<string, string> }) {
  const tiles: [string, string][] = [["청크", `${s.chunks} / ${s.corpus_chunks}`], ["dense 차원", String(s.dense_dim)], ["sparse 비영 평균", String(s.sparse_nnz)], ["어휘(명사)", String(s.vocab)]];
  return (
    <>
      <div className="stats">{tiles.map(([l, n]) => <div key={l} className="stat"><div className="n">{n}</div><div className="l">{l}</div></div>)}</div>
      <div className="tablewrap"><table><tbody>
        <tr><td className="k">쪽 수</td><td>{Object.entries(s.pages).map(([d, n]) => `${titles[d] ?? d} ${n}`).join(" · ")}</td></tr>
        <tr><td className="k">청크</td><td>{String(s.chunk_size)}자 · 겹침 {String(s.overlap)}자 · 평균 {String(s.avg_chunk_chars)}자</td></tr>
        <tr><td className="k">임베딩</td><td>{String(s.embedding)}</td></tr>
        <tr><td className="k">색인</td><td><code>{(s.collections as string[]).join(" · ")}</code> (Qdrant, named vector dense+sparse)</td></tr>
        <tr><td className="k">지도</td><td>{String(s.map_method)}</td></tr>
        <tr><td className="k">topic</td><td>{String(s.topic_method)}</td></tr>
        <tr><td className="k">말뭉치</td><td>문서 {String(s.corpus_docs)}개 · 청크 {String(s.corpus_chunks)}개 · 이 범위 토큰 {String(s.tokens)}</td></tr>
      </tbody></table></div>
    </>
  );
}
