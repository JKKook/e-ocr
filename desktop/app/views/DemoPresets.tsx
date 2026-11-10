"use client";
import { useEffect, useState } from "react";
import { DEMO, getDemoPresets } from "../lib/api";

/** 정적 데모에서만 — 녹화해 둔 예시 질문 버튼. 데스크톱 앱에서는 아무것도 그리지 않는다. */
export function DemoPresets({ onPick, label = "데모 — 녹화된 예시 질문을 눌러 보세요" }: { onPick: (q: string) => void; label?: string }) {
  const [qs, setQs] = useState<string[]>([]);
  useEffect(() => { if (DEMO) getDemoPresets().then(setQs).catch(() => {}); }, []);
  if (!DEMO || !qs.length) return null;
  return (
    <section className="demo-presets" aria-label="예시 질문">
      <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>{label}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {qs.map((q) => <button key={q} className="btn" onClick={() => onPick(q)}>{q}</button>)}
      </div>
    </section>
  );
}
