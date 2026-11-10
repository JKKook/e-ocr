"use client";
import { useState } from "react";
import { COLLECTION_LABEL, COLLECTIONS, MODE_LABEL, MODES, search, type Hit, type Mode } from "../lib/api";
import { Hits, Spinner } from "./Hits";

type Col = { sec: number; hits: Hit[] } | "busy" | { err: string };

export function CompareView() {
  const [q, setQ] = useState("");
  const [col, setCol] = useState<string>("vlm_ctx");
  const [res, setRes] = useState<Partial<Record<Mode, Col>>>({});
  const busy = Object.values(res).some((v) => v === "busy");

  async function run() {
    if (!q.trim()) return;
    setRes(Object.fromEntries(MODES.map((m) => [m, "busy"])));
    // 같은 임베딩 모델을 쓰므로 순서대로 — 결과가 오는 대로 칸을 채운다.
    for (const m of MODES) {
      try { const r = await search(q, col, m); setRes((p) => ({ ...p, [m]: r })); }
      catch (e) { setRes((p) => ({ ...p, [m]: { err: String(e) } })); }
    }
  }

  return (
    <>
      <div className="panel">
        <form className="ask" onSubmit={(e) => { e.preventDefault(); run(); }}>
          <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder="같은 질문을 검색 방식 4가지로 나란히 비교합니다" autoFocus />
          <select value={col} onChange={(e) => setCol(e.target.value)}>
            {COLLECTIONS.map((c) => <option key={c} value={c}>{COLLECTION_LABEL[c]}</option>)}
          </select>
          <button className="btn primary" disabled={busy || !q.trim()}>비교</button>
        </form>
      </div>
      <div className="cols" style={{ marginTop: 12 }}>
        {MODES.map((m) => {
          const r = res[m];
          return (
            <div className="panel" key={m} style={{ marginTop: 0 }}>
              <h3>{MODE_LABEL[m]} {r && typeof r === "object" && "sec" in r && <span className="muted mono">{r.sec}s</span>}</h3>
              {!r && <div className="muted">질문을 넣으면 top-5 가 나옵니다</div>}
              {r === "busy" && <Spinner label="검색 중" />}
              {r && typeof r === "object" && "err" in r && <div className="err">{r.err}</div>}
              {r && typeof r === "object" && "hits" in r && <Hits hits={r.hits} showCtx={false} />}
            </div>
          );
        })}
      </div>
    </>
  );
}
