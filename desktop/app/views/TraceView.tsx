"use client";
import { useEffect, useState } from "react";
import { getTraces, getTraceStats, type TraceRow, type TraceStats } from "../lib/api";

const STAGES = [["condense_ms", "질문 재작성"], ["embed_ms", "임베딩"], ["search_ms", "벡터 검색"], ["rerank_ms", "rerank"], ["gen_ms", "생성"], ["total_ms", "전체"]] as const;
const CONF: Record<string, [string, string]> = { high: ["ok", "높음"], low: ["warn", "낮음"], none: ["", "거절"] };
const fmt = (ms: number | null | undefined) => (ms == null ? "–" : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);

export function TraceView() {
  const [stats, setStats] = useState<TraceStats | null>(null);
  const [rows, setRows] = useState<TraceRow[]>([]);
  const [sel, setSel] = useState<number | null>(null);

  const load = () => { getTraceStats().then(setStats).catch(() => {}); getTraces().then(setRows).catch(() => {}); };
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  const it = rows.find((r) => r.id === sel) ?? rows[0];
  const maxTotal = Math.max(1, ...rows.map((r) => r.stages.total_ms ?? 0));

  if (!stats) return <div className="panel muted">불러오는 중…</div>;
  return (
    <>
      <div className="stats">
        <div className="stat"><div className="n">{stats.n}</div><div className="l">기록된 질의</div></div>
        <div className="stat"><div className="n">{fmt(stats.stages.total_ms.p50)} / {fmt(stats.stages.total_ms.p95)}</div><div className="l">전체 지연 p50 / p95</div></div>
        <div className="stat"><div className="n">{stats.refused} · {stats.low}</div><div className="l">거절 · 확신 낮음</div></div>
        <div className="stat"><div className="n">{stats.flagged} · {stats.retried}</div><div className="l">검사 경고 남음 · 재생성</div></div>
        <div className="stat"><div className="n">👍 {stats.feedback.up} · 👎 {stats.feedback.down}</div><div className="l">사용자 피드백 · 비용 ${stats.usd}</div></div>
      </div>

      <div className="panel">
        <h3>단계별 지연</h3>
        <table>
          <thead><tr><th>단계</th><th className="num">p50</th><th className="num">p95</th><th className="num">표본</th></tr></thead>
          <tbody>{STAGES.map(([k, l]) => <tr key={k}><td className="t">{l}</td><td className="num">{fmt(stats.stages[k]?.p50)}</td><td className="num">{fmt(stats.stages[k]?.p95)}</td><td className="num">{stats.stages[k]?.n}</td></tr>)}</tbody>
        </table>
      </div>

      <div className="split" style={{ marginTop: 12 }}>
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>#</th><th>질문</th><th>확신</th><th>모델</th><th style={{ width: "28%" }}>전체 시간</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={r.id === it?.id ? "sel" : ""} onClick={() => setSel(r.id)}>
                  <td>{r.id}</td>
                  <td className="t">{r.q}{r.feedback ? (r.feedback > 0 ? " 👍" : " 👎") : ""}</td>
                  <td><span className={`pill ${CONF[r.confidence][0]}`}>{CONF[r.confidence][1]}</span>{Object.keys(r.issues).length > 0 && <> <span className="pill warn">경고</span></>}</td>
                  <td>{r.llm}</td>
                  <td><div className="bar"><i style={{ width: `${((r.stages.total_ms ?? 0) / maxTotal) * 100}%` }} /></div><span className="muted">{fmt(r.stages.total_ms)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <div className="muted" style={{ padding: 14 }}>아직 기록이 없어요. 대화 화면에서 질문하면 여기에 쌓입니다.</div>}
        </div>
        {it && (
          <div className="panel sticky">
            <h3>#{it.id} · {new Date(it.ts * 1000).toLocaleString("ko-KR")}</h3>
            <table><tbody>
              <tr><td className="k">질문</td><td className="t">{it.q}</td></tr>
              {it.standalone !== it.q && <tr><td className="k">재작성</td><td className="t">{it.standalone}</td></tr>}
              <tr><td className="k">확신</td><td>{CONF[it.confidence][1]} · top {it.top_score.toFixed(3)}</td></tr>
              <tr><td className="k">모델</td><td>{it.llm}{it.retried ? " · 재생성 1회" : ""} · 토큰 {it.in_tok ?? "–"}→{it.out_tok ?? "–"} · ${it.usd}</td></tr>
              <tr><td className="k">검사</td><td>{Object.keys(it.issues).length ? <code>{JSON.stringify(it.issues)}</code> : "통과"}</td></tr>
              <tr><td className="k">근거</td><td><code>{it.hits.join(" · ")}</code></td></tr>
              {STAGES.map(([k, l]) => <tr key={k}><td className="k">{l}</td><td>{fmt(it.stages[k])}</td></tr>)}
            </tbody></table>
            <div className="answer" style={{ fontSize: 14, marginTop: 10 }}>{it.answer}</div>
          </div>
        )}
      </div>
    </>
  );
}
