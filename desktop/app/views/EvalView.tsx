"use client";
import { useState } from "react";
import type { LlmAnswer, Report } from "../lib/api";

function Verdict({ a }: { a: LlmAnswer }) {
  const c = a.judge?.correct ?? 0;
  return (
    <>
      <span className={`pill ${c === 2 ? "ok" : "warn"}`}>{c}/2</span>{" "}
      {a.judge && !a.judge.grounded && <span className="pill warn">근거 밖</span>}
    </>
  );
}

export function EvalView({ r }: { r: Report }) {
  const [sel, setSel] = useState(r.answers[0]?.qid);
  const it = r.answers.find((a) => a.qid === sel);

  return (
    <div className="split">
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>#</th><th>질문</th><th>로컬</th><th>Claude</th></tr></thead>
          <tbody>
            {r.answers.map((a) => (
              <tr key={a.qid} className={a.qid === sel ? "sel" : ""} onClick={() => setSel(a.qid)}>
                <td>{a.qid}</td><td className="t">{a.q}</td><td><Verdict a={a.local} /></td><td><Verdict a={a.claude} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {it && (
        <div className="sticky">
          <div className="panel">
            <h3>{it.qid} · {it.q}</h3>
            <div className="muted" style={{ fontSize: 12 }}>정답</div>
            <div className="answer" style={{ fontSize: 14 }}>{it.gold}</div>
            <div className="muted mono" style={{ marginTop: 8 }}>근거 청크 {it.ctx_ids.join(" · ")}</div>
          </div>
          {(["local", "claude"] as const).map((who) => (
            <div className="panel" key={who}>
              <h3>{who === "local" ? "qwen2.5:7b · 로컬" : "Claude Sonnet"} <Verdict a={it[who]} /> <span className="muted mono">{it[who].sec}s</span></h3>
              <div className="answer" style={{ fontSize: 14 }}>{it[who].text}</div>
              {it[who].judge && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>채점 메모 — {it[who].judge!.why}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
