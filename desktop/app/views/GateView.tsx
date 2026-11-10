"use client";
import { useEffect, useState } from "react";
import { getGate, type Gate } from "../lib/api";

const LABEL: Record<string, string> = {
  base30: "본 문항 30", synth_dev: "합성 dev", synth_holdout: "합성 holdout", vague: "막연한 질문 12",
  false_refusal: "잘못된 거절률 (낮을수록 좋음)", true_refusal: "답 없는 질문 거절률",
};
const name = (m: string) => { const [s, k] = m.split("."); return k ? `${LABEL[s] ?? s} · ${k}` : LABEL[m] ?? m; };

export function GateView() {
  const [g, setG] = useState<Gate | undefined>(undefined);
  useEffect(() => { getGate().then(setG).catch(() => setG(null)); }, []);
  if (g === undefined) return <div className="panel muted">불러오는 중…</div>;
  if (!g) return <div className="panel">아직 게이트를 돌리지 않았어요. <code>uv run python -m aiocr.gate</code></div>;

  return (
    <>
      <div className={`banner ${g.pass ? "ok" : "no"}`}>
        <b>{g.pass ? "게이트 통과" : "게이트 실패"}</b>
        <span>{g.checks.filter((c) => !c.pass).length}개 지표가 허용폭을 넘었습니다 · 측정 {g.when.replace("T", " ")} · 기준선 {g.baseline_when.replace("T", " ")}</span>
      </div>
      <div className="panel" style={{ marginTop: 12 }}>
        <h3>설정 <code>{Object.entries(g.config).map(([k, v]) => `${k}=${v}`).join(" · ")}</code></h3>
        <table>
          <thead><tr><th>지표</th><th className="num">기준선</th><th className="num">지금</th><th className="num">변화</th><th className="num">허용폭</th><th className="num">표본</th><th>판정</th></tr></thead>
          <tbody>
            {g.checks.map((c) => (
              <tr key={c.metric}>
                <td className="t">{name(c.metric)}</td>
                <td className="num">{c.baseline.toFixed(3)}</td><td className="num">{c.now.toFixed(3)}</td>
                <td className="num">{c.delta > 0 ? "+" : ""}{c.delta.toFixed(3)}</td><td className="num">±{c.tol}</td>
                <td className="num">{g.n[c.metric]}</td>
                <td><span className={`pill ${c.pass ? "ok" : "warn"}`}>{c.pass ? "통과" : "실패"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
          허용폭 = max(0.02, 1/n) — 한 문항이 흔들리는 정도. 실패하면 <code>python -m aiocr.gate</code> 가 종료 코드 1 을 돌려 배포를 막습니다.
          holdout 은 가드·임계값을 맞출 때 쓰지 않은 문항입니다.
        </p>
      </div>
    </>
  );
}
