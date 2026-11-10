"use client";
import { Fragment, useEffect, useState } from "react";
import { getJob, getPolicy, setSegments, type Part, type Policy } from "../lib/api";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** 정책(SSOT) 범례 — policy/transcript.json 의 분류·규칙을 그대로 보여 준다. */
export function PolicyLegend({ p }: { p: Policy | null }) {
  if (!p) return null;
  return (
    <div className="tlegend" aria-label="대본 정책">
      <b>대본 정책 v{p.version}</b>
      {[...p.line_kinds, ...p.span_kinds].map((k) => (
        <span key={k.id} className={`lg k-${k.id}`} title={k.rule}>{k.label}<em>{k.translate === "allow" ? "번역·요약 가능" : k.translate === "pair" ? "원문+번역" : k.translate === "drop" ? "내용 아님" : "원문 그대로"}</em></span>
      ))}
    </div>
  );
}

export function usePolicy() {
  const [p, setP] = useState<Policy | null>(null);
  useEffect(() => { getPolicy().then(setP).catch(() => {}); }, []);
  return p;
}

/** 줄 하나 — 고유명은 밑줄, 자막 오류 의심은 [?→추정], 효과 표시는 배지. */
function LineText({ part }: { part: Part }) {
  if (part.k === "cue") return <span className="cuebadge">{part.t.trim()}</span>;
  const marks = [
    ...(part.garbled ?? []).map((g) => ({ w: g.orig, kind: "garbled" as const, guess: g.guess })),
    ...(part.proper ?? []).map((w) => ({ w, kind: "proper" as const, guess: "" })),
  ].filter((m) => m.w);
  if (!marks.length) return <>{part.t}</>;
  const re = new RegExp(`(${marks.map((m) => esc(m.w)).join("|")})`, "g");
  return (
    <>
      {part.t.split(re).map((x, i) => {
        const m = marks.find((mm) => mm.w === x);
        if (!m) return <Fragment key={i}>{x}</Fragment>;
        return m.kind === "garbled"
          ? <span key={i} className="garb" title={`자막 원문 그대로 둠 — '${m.guess}'로 추정`}>{x}<sup>?→{m.guess}</sup></span>
          : <span key={i} className="prop" title="고유명·상표 — 표기 유지">{x}</span>;
      })}
    </>
  );
}

/** 영상 구간 대본 — 줄마다 분류 스타일 + 분류 고치기(고치면 그 문서만 다시 색인). */
export function TranscriptLines({ doc, parts, policy, onReindexed }: { doc: string; parts: Part[]; policy: Policy | null; onReindexed: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  async function change(s: number, kind: string) {
    setBusy(`${s}`);
    try {
      const job = await setSegments(doc, [s], kind);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const j = await getJob(job.id);
        if (j.state === "ok" || j.state === "error") break;
      }
      onReindexed();
    } finally { setBusy(null); }
  }
  // 이어진 가사 줄은 한 덩어리로
  const groups: { k: string; items: Part[] }[] = [];
  for (const p of parts) {
    const last = groups.at(-1);
    if (last && last.k === "lyrics" && p.k === "lyrics") last.items.push(p);
    else groups.push({ k: p.k, items: [p] });
  }
  return (
    <div className="tlines">
      {groups.map((g, gi) => (
        <div key={gi} className={`tgroup k-${g.k}`}>
          {g.k === "lyrics" && <div className="tglabel">♪ 가사 · 원문 그대로</div>}
          {g.items.map((p) => (
            <div key={p.s} className="tline">
              <span className="ts mono">{mmss(p.s)}</span>
              <span className="tt">{g.k === "foreign" && <span className="fl">외국어 원문</span>}<LineText part={p} /></span>
              {policy && (
                <select className="kindsel" aria-label={`${mmss(p.s)} 줄 분류`} value={p.k} disabled={!!busy}
                  onChange={(e) => change(p.s, e.target.value)}>
                  {policy.line_kinds.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                  <option value="auto">자동 판별로</option>
                </select>
              )}
              {busy === `${p.s}` && <span className="spin" aria-label="다시 색인 중" />}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
