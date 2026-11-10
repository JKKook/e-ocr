"use client";
import { Fragment } from "react";
import { COLLECTION_LABEL, MODE_LABEL, MODES, type LlmAnswer, type Report, type Scorecard } from "../lib/api";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const f3 = (x: number) => x.toFixed(3);

function summary(xs: LlmAnswer[]) {
  const n = xs.length;
  return {
    score: xs.reduce((s, x) => s + (x.judge?.correct ?? 0), 0) / (2 * n),
    grounded: xs.filter((x) => x.judge?.grounded).length / n,
    sec: xs.reduce((s, x) => s + x.sec, 0) / n,
    usd: xs.reduce((s, x) => s + x.usd, 0),
  };
}

const ev = (v: unknown) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : v === null || v === undefined ? "없음" : String(v));

/** 개념별 채점표 — 점수는 eval/scorecard.json(판단), 근거 수치는 결과 파일에서 매번 다시 읽는다. */
function ScorecardPanel({ sc }: { sc: Scorecard }) {
  const prev = sc.history.at(-2);
  const stale = sc.items.filter((it) => it.stale.length).length;
  return (
    <div className="panel" style={{ marginTop: 0, marginBottom: 12 }}>
      <h3>개념 채점표 — {sc.total} / {sc.max}
        {prev && <span className="muted" style={{ fontWeight: 400 }}> · 지난 채점 {prev.total}</span>}
        {stale > 0 && <span className="pill warn" style={{ marginLeft: 8 }}>재채점 필요 {stale}</span>}</h3>
      <table>
        <thead><tr><th>개념</th><th className="num">점수</th><th style={{ width: "12%" }}></th><th className="num">구현·측정·한계</th>
          <th>근거 (지금 값)</th><th>감점 이유</th><th>다음 실험</th></tr></thead>
        <tbody>
          {sc.items.map((it) => (
            <tr key={it.id} className={it.stale.length ? "stale" : ""}>
              <td className="t" title={it.def}>{it.term}{it.stale.length > 0 && <div className="pill warn" title={it.stale.join(", ")}>재채점</div>}</td>
              <td className="num">{it.total}</td>
              <td><div className="bar"><i style={{ width: `${it.total * 10}%` }} /></div></td>
              <td className="num mono" style={{ whiteSpace: "nowrap" }}>{it.score.impl}/4 · {it.score.measure}/4 · {it.score.limits}/2</td>
              <td style={{ fontSize: 12 }}>{it.evidence.length ? it.evidence.map((e) => (
                <div key={e.path}>{e.label} <b className="mono">{ev(e.now)}</b>
                  {"seen" in e && e.seen !== e.now && <span className="muted"> (채점 때 {ev(e.seen)})</span>}</div>)) : <span className="muted">측정 없음</span>}</td>
              <td style={{ fontSize: 12 }}>{it.gap}</td>
              <td style={{ fontSize: 12 }}>{it.next ? <>{it.next.do} <span className="muted">(+{it.next.gain}{it.next.expects ? ` · ${it.next.expects}` : ""})</span></> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>{sc.rubric}</p>
      <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>고도화 순서: {sc.how_to_update}</p>
      {sc.history.length > 0 && (
        <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>채점 이력: {sc.history.map((h) => `${h.when.slice(0, 10)} ${h.total}점(${h.note})`).join(" → ")}</p>
      )}
    </div>
  );
}

export function ReportView({ r }: { r: Report }) {
  const ret = r.retrieval;
  const base = ret["vlm_base|dense"], rr = ret["vlm_base|hybrid+rerank"], ctx = ret["vlm_ctx|dense"];
  const loc = summary(r.answers.map((a) => a.local)), cl = summary(r.answers.map((a) => a.claude));
  const best = Math.max(...Object.values(ret).map((m) => m["hit@1"]));

  return (
    <>
      {r.scorecard && <ScorecardPanel sc={r.scorecard} />}
      <div className="stats">
        <div className="stat"><div className="n">{f3(base["hit@1"])} → {f3(rr["hit@1"])}</div><div className="l">Hit@1 · dense → hybrid+rerank (VLM 기본 색인)</div></div>
        <div className="stat"><div className="n">{f3(base["hit@1"])} → {f3(ctx["hit@1"])}</div><div className="l">Hit@1 · dense 에 contextual 맥락 추가</div></div>
        <div className="stat"><div className="n">{f3(cl.score)} / {f3(loc.score)}</div><div className="l">답변 점수 · Claude Sonnet / qwen2.5:7b (Opus 채점)</div></div>
        <div className="stat"><div className="n">{base.n}</div><div className="l">평가 문항 수 · 1문항 = {pct(1 / base.n)}p</div></div>
      </div>

      <div className="panel">
        <h3>검색 ablation — 색인 4 × 방식 4</h3>
        <table>
          <thead><tr><th>색인</th><th>방식</th><th className="num">Hit@1</th><th style={{ width: "18%" }}></th><th className="num">Recall@5</th><th style={{ width: "18%" }}></th><th className="num">MRR@10</th><th className="num">초/질문</th></tr></thead>
          <tbody>
            {["vlm_base", "rapidocr_base", "vlm_ctx", "rapidocr_ctx"].map((c) => (
              <Fragment key={c}>
                {MODES.map((m, i) => {
                  const x = ret[`${c}|${m}`];
                  return (
                    <tr key={m} className={x["hit@1"] === best ? "best" : ""}>
                      <td className="t">{i === 0 ? COLLECTION_LABEL[c] : ""}</td>
                      <td>{MODE_LABEL[m]}</td>
                      <td className="num">{f3(x["hit@1"])}</td>
                      <td><div className="bar"><i style={{ width: pct(x["hit@1"]) }} /></div></td>
                      <td className="num">{f3(x["recall@5"])}</td>
                      <td><div className="bar alt"><i style={{ width: pct(x["recall@5"]) }} /></div></td>
                      <td className="num">{f3(x["mrr@10"])}</td>
                      <td className="num">{x.sec_per_q}</td>
                    </tr>
                  );
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>주황 막대 = Hit@1, 회색 막대 = Recall@5, 강조 행 = Hit@1 최고. rerank 가 가장 큰 상승을 만들었고 sparse 를 섞은 hybrid 는 dense 를 넘지 못했다.</p>
      </div>

      <div className="cols" style={{ marginTop: 12 }}>
        <div className="panel" style={{ marginTop: 0 }}>
          <h3>OCR 문자 오류율 (합성 스캔, 정답 텍스트 대비)</h3>
          <table>
            <thead><tr><th>문서</th><th>엔진</th><th className="num">CER</th><th className="num">초/쪽</th></tr></thead>
            <tbody>
              {Object.entries(r.ocr).flatMap(([d, es]) => Object.entries(es).map(([e, v], i) => (
                <tr key={d + e}><td>{i === 0 ? d : ""}</td><td>{e === "vlm" ? "qwen2.5vl:7b" : "RapidOCR"}</td>
                  <td className="num">{pct(v.cer)}</td><td className="num">{v.sec_per_page}</td></tr>
              )))}
            </tbody>
          </table>
        </div>
        <div className="panel" style={{ marginTop: 0 }}>
          <h3>답변 모델 비교 (같은 top-5 근거)</h3>
          <table>
            <thead><tr><th>모델</th><th className="num">점수</th><th className="num">근거 충실</th><th className="num">평균 초</th><th className="num">비용 30문항</th></tr></thead>
            <tbody>
              <tr><td>Claude Sonnet</td><td className="num">{f3(cl.score)}</td><td className="num">{pct(cl.grounded)}</td><td className="num">{cl.sec.toFixed(2)}</td><td className="num">${cl.usd.toFixed(3)}</td></tr>
              <tr><td>qwen2.5:7b</td><td className="num">{f3(loc.score)}</td><td className="num">{pct(loc.grounded)}</td><td className="num">{loc.sec.toFixed(2)}</td><td className="num">$0</td></tr>
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>채점 = Claude Opus (Claude 답을 Claude 가 채점하는 편향 가능). 문항별 내용은 「평가 문항」 화면.</p>
        </div>
      </div>
    </>
  );
}
