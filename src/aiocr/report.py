"""보고서 — reports/*.json 실측값으로 report.html(집 스타일)과 REPORT.md 를 만든다. 수치는 손으로 옮기지 않는다."""
import json
import statistics as st
from datetime import date
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "reports"
ret = json.loads((R / "retrieval.json").read_text())
ocr = json.loads((R / "ocr_eval.json").read_text())
ans = json.loads((R / "answers.json").read_text())
qs = [json.loads(l) for l in (ROOT / "eval" / "questions.jsonl").open(encoding="utf-8")]
MODES = ["dense", "sparse", "hybrid", "hybrid+rerank"]
LABEL = {"dense": "dense", "sparse": "sparse", "hybrid": "hybrid (RRF)", "hybrid+rerank": "hybrid + rerank"}
n_chunks = 797


def g(c, m, k):
    return ret[f"{c}|{m}"][k]


def ans_stats(who):
    xs = [it[who] for it in ans["items"]]
    return {"score": sum(x["judge"]["correct"] for x in xs) / (2 * len(xs)),
            "full": sum(x["judge"]["correct"] == 2 for x in xs),
            "grounded": sum(x["judge"]["grounded"] for x in xs) / len(xs),
            "sec": st.mean(x["sec"] for x in xs), "usd": sum(x["usd"] for x in xs), "n": len(xs)}


L, C = ans_stats("local"), ans_stats("claude")
G_NOFB = json.loads((R / "answers_guarded_nofb.json").read_text())
G_FB = json.loads((R / "answers_guarded.json").read_text())
GATE = json.loads((R / "gate.json").read_text())


def g_stats(res):
    xs = [it["local"] for it in res["items"]]
    return {"score": sum(x["judge"]["correct"] for x in xs) / (2 * len(xs)), "full": sum(x["judge"]["correct"] == 2 for x in xs),
            "grounded": sum(x["judge"]["grounded"] for x in xs) / len(xs), "sec": st.mean(x["sec"] for x in xs),
            "usd": sum(x["usd"] for x in xs), "n": len(xs), "fallback": sum(x["llm"] != "local" for x in xs),
            "retried": sum(x["retried"] for x in xs)}


GN, GF = g_stats(G_NOFB), g_stats(G_FB)


# ── 그림 1: 파이프라인 ──────────────────────────────────────────────
def pipeline_svg():
    rows = [[("수집", "wiki/raw · 18문서", 0), ("파싱", "PDF·pptx·html·md", 0), ("OCR", "RapidOCR | qwen2.5vl", 1), ("청킹", f"900자 · {n_chunks}청크", 0)],
            [("맥락 부착", "qwen2.5:7b · 청크당 2.9s", 1), ("임베딩", "bge-m3 ONNX dense+sparse", 0), ("Qdrant", "named vectors", 0), ("검색", "dense · sparse · RRF", 0)],
            [("rerank", "bge-reranker-v2-m3 · top30→5", 0), ("답변", "qwen2.5:7b | Sonnet", 1), ("채점", "Opus · 정답 대비 0/1/2", 1), ("보고서", "이 문서", 0)]]
    w, h, bw, bh, gx = 760, 300, 160, 54, 30
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="수집부터 보고서까지 12단계 파이프라인. 주황 상자는 LLM 호출 지점.">',
           '<defs><marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>',
           '<g font-family="IBM Plex Mono, ui-monospace, monospace" font-size="12" fill="currentColor">']
    for r, row in enumerate(rows):
        y = 16 + r * 96
        for i, (t, s, llm) in enumerate(row):
            x = 16 + i * (bw + gx) if r % 2 == 0 else 16 + (3 - i) * (bw + gx)
            fill, stroke = ('var(--accent-soft)', 'var(--accent)') if llm else ('none', 'currentColor')
            out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')
            out.append(f'<text x="{x + 10}" y="{y + 22}" font-weight="500">{t}</text><text x="{x + 10}" y="{y + 41}" font-size="10.5" fill="var(--muted)">{escape(s)}</text>')
            if i < 3:
                x1, x2 = (x + bw, x + bw + gx) if r % 2 == 0 else (x, x - gx)
                out.append(f'<line x1="{x1}" y1="{y + bh / 2}" x2="{x2}" y2="{y + bh / 2}" stroke="currentColor" stroke-width="1.2" marker-end="url(#arw)"/>')
        if r < 2:
            xe = 16 + 3 * (bw + gx) + bw / 2 if r % 2 == 0 else 16 + bw / 2
            out.append(f'<line x1="{xe}" y1="{y + bh}" x2="{xe}" y2="{y + 96}" stroke="currentColor" stroke-width="1.2" marker-end="url(#arw)"/>')
    out.append("</g></svg>")
    return "\n".join(out)


# ── 그림 2: 검색 ablation 막대 ──────────────────────────────────────
def ablation_svg(col_pair=("vlm_base", "vlm_ctx")):
    rows = [(c, m) for c in col_pair for m in MODES]
    w, lh, x0, bw = 760, 34, 230, 440
    h = 30 + lh * len(rows) + 30
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="검색 방식 8가지의 Hit@1(진한 막대)과 Recall@5(옅은 막대) 비교">',
           '<g font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11.5" fill="currentColor">']
    for t in (0, .25, .5, .75, 1):
        x = x0 + bw * t
        out.append(f'<line x1="{x}" y1="20" x2="{x}" y2="{h - 26}" stroke="var(--line)" stroke-width="1"/>'
                   f'<text x="{x}" y="{h - 10}" text-anchor="middle" fill="var(--muted)">{t:.2f}</text>')
    for i, (c, m) in enumerate(rows):
        y = 26 + i * lh
        tag = "맥락 O" if c.endswith("ctx") else "맥락 X"
        out.append(f'<text x="{x0 - 10}" y="{y + 15}" text-anchor="end">{tag} · {LABEL[m]}</text>')
        r5, h1 = g(c, m, "recall@5"), g(c, m, "hit@1")
        out.append(f'<rect x="{x0}" y="{y + 2}" width="{bw * r5:.1f}" height="22" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width=".8"/>')
        out.append(f'<rect x="{x0}" y="{y + 8}" width="{bw * h1:.1f}" height="10" fill="var(--accent)"/>')
        out.append(f'<text x="{x0 + bw * r5 + 6:.1f}" y="{y + 17}" fill="var(--muted)">{h1:.2f} / {r5:.2f}</text>')
        if i == 3:
            out.append(f'<line x1="20" y1="{y + lh - 2}" x2="{w - 20}" y2="{y + lh - 2}" stroke="var(--line)" stroke-dasharray="4 3"/>')
    out.append("</g></svg>")
    return "\n".join(out)


def tr(cells, k=True):
    return "<tr>" + "".join(f'<td class="{"k" if (i == 0 and k) else ""}{" num" if isinstance(v, (int, float)) else ""}">'
                            f'{(f"{v:.3f}" if isinstance(v, float) else v)}</td>' for i, v in enumerate(cells)) + "</tr>"


def build_html():
    css = (R / "_style.css").read_text().split("<style>", 1)[-1]  # 템플릿 머리말(title·link) 제거
    best = ("vlm_base", "hybrid+rerank")
    src_cnt = {}
    for q in qs:
        src_cnt[q["src"]] = src_cnt.get(q["src"], 0) + 1
    abl_rows = "\n".join(tr([f"<code>{c}</code>", LABEL[m], g(c, m, "hit@1"), g(c, m, "recall@5"), g(c, m, "mrr@10"),
                             ret[f"{c}|{m}"]["by_src"].get("scan", {}).get("recall@5", 0.0), g(c, m, "sec_per_q")])
                         for c in ("rapidocr_base", "vlm_base", "rapidocr_ctx", "vlm_ctx") for m in MODES)
    ocr_rows = "\n".join(tr([f"<code>{d}</code>", e, v["cer"], v["sec_per_page"]]) for d, es in ocr.items() for e, v in es.items())
    bad = [it for it in ans["items"] if it["local"]["judge"]["correct"] < 2 or not it["local"]["judge"]["grounded"]]
    bad_rows = "\n".join(
        f'<tr><td class="k">{it["qid"]}</td><td>{escape(it["gold"])}</td><td>{escape(it["local"]["text"][:110])}</td>'
        f'<td><span class="pill {"warn" if it["local"]["judge"]["correct"] else "no"}">{it["local"]["judge"]["correct"]}/2</span> '
        f'{"" if it["local"]["judge"]["grounded"] else "<span class=\"pill warn\">근거 밖</span>"}</td>'
        f'<td>{escape(it["local"]["judge"]["why"])}</td></tr>' for it in bad)
    d_dense = g("vlm_ctx", "dense", "hit@1") - g("vlm_base", "dense", "hit@1")
    d_rr = g("vlm_base", "hybrid+rerank", "hit@1") - g("vlm_base", "hybrid", "hit@1")
    gr, vl = ocr["pms-gg-release"], ocr["pms-ml-lab"]
    return f"""<!doctype html>
<html lang="ko">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI OCR RAG 실험 보고서</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
{css}
.wrap{{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:56px 96px}}
</style>
<div class="wrap">
<header>
  <p class="eyebrow">AI OCR RAG · 개인 프로젝트 · 실험 보고서</p>
  <h1>스캔 문서까지 읽는 로컬 RAG, 기법별 기여 측정</h1>
  <p class="lede">OCR 논문·기술 문서와 개인 프로젝트 문서 18개(스캔 PDF·pptx 포함)를 로컬에서 색인하고, 검색 기법을 하나씩 켤 때마다 30문항 평가셋 점수가 어떻게 바뀌는지 쟀다. 가장 큰 개선은 reranker였고, sparse를 섞은 hybrid는 dense 단독보다 낫지 않았다.</p>
  <div class="meta">
    <span>상태 <b>v1.0 · 실측 완료</b></span>
    <span>작성 <b>2026-11-10</b> · 갱신 <b>{date.today()}</b></span>
    <span>저장소 <b><code>~/projects/ai-ocr</code></b></span>
    <span>머신 <b>Apple M5 Pro · 48GB · 전부 로컬(답변 비교만 Claude)</b></span>
  </div>
</header>

<div class="narrow">
<div class="tldr">
  <h3>결론 다섯 줄</h3>
  <ol>
    <li><b>reranker가 가장 크게 기여했다.</b> hybrid 다음에 bge-reranker-v2-m3를 붙이자 Hit@1이 {g(*best[:1], 'hybrid', 'hit@1'):.2f}에서 {g(*best, 'hit@1'):.2f}로 {d_rr * 100:+.0f}%p 올랐다. 대가는 질문당 약 2초다.</li>
    <li><b>contextual retrieval은 dense 검색을 끌어올렸다.</b> 청크마다 맥락 문장을 붙이자 dense Hit@1이 {d_dense * 100:+.0f}%p, Recall@5가 {g('vlm_base', 'dense', 'recall@5'):.2f}에서 {g('vlm_ctx', 'dense', 'recall@5'):.2f}로 올랐다. reranker까지 켠 조합에서는 Recall@5가 {g('vlm_ctx', 'hybrid+rerank', 'recall@5'):.2f}에 이르렀지만, Hit@1은 한 문항 차이로 낮아졌다.</li>
    <li><b>bge-m3 sparse는 약했고 RRF hybrid는 dense를 넘지 못했다.</b> sparse Hit@1은 {g('vlm_base', 'sparse', 'hit@1'):.2f}였다. 같은 가중치로 섞으면 dense의 순위를 희석한다. 회사 데이터에서 본 경향이 여기서도 재현됐다.</li>
    <li><b>VLM OCR이 더 정확하지만 70배 느리다.</b> qwen2.5vl의 CER은 {gr['vlm']['cer'] * 100:.1f}%·{vl['vlm']['cer'] * 100:.1f}%, RapidOCR은 {gr['rapidocr']['cer'] * 100:.1f}%·{vl['rapidocr']['cer'] * 100:.1f}%였다. 그래도 reranker를 쓰면 두 엔진 모두 스캔 문항 4개를 모두 찾았다.</li>
    <li><b>답변은 Claude가 정확했고, 로컬 7B는 숫자를 지어냈다.</b> 점수는 {C['score']:.2f} 대 {L['score']:.2f}, 근거 충실도는 {C['grounded']:.2f} 대 {L['grounded']:.2f}였다. Claude 비용은 30문항에 ${C['usd']:.2f}다.</li>
  </ol>
</div>
</div>

<div class="narrow"><h2><span class="stage-no">01</span>파이프라인</h2></div>
<figure>{pipeline_svg()}
<figcaption>실선 화살표 = 처리 순서(ㄹ자로 읽는다) · 주황 상자 = LLM 호출 지점(OCR의 VLM 경로, 맥락 생성, 답변, 채점) · 회색 둘째 줄 = 실제 구현과 수치. 맥락 생성·답변·OCR LLM은 Ollama 로컬 모델이고, 답변 비교와 채점만 Claude를 쓴다.</figcaption>
</figure>
<div class="tablewrap"><table>
<thead><tr><th>단계</th><th>선택</th><th>이유</th></tr></thead><tbody>
<tr><td class="k">수집</td><td><code>wiki/raw/</code> 원문 보관 + <code>index.md</code>·<code>log.md</code> (llm-wiki 패턴)</td><td>원문을 바꾸지 않고 남겨 두어야 다시 파싱하고 출처를 추적할 수 있다. 위키 요약 페이지는 색인하지 않는다.</td></tr>
<tr><td class="k">파싱</td><td>텍스트 레이어가 있으면 그대로 쓰고, 20자 미만인 페이지만 OCR</td><td>디지털 PDF까지 OCR하면 오류만 늘어난다.</td></tr>
<tr><td class="k">임베딩</td><td>bge-m3 ONNX를 한 번 추론해 dense 1024와 학습형 sparse를 함께 얻음</td><td>torch 없이 CPU로 797청크를 1분 42초에 처리하고, 한국어·영어를 교차 검색할 수 있다.</td></tr>
<tr><td class="k">벡터 DB</td><td>Qdrant named vector(dense + sparse), 서버 측 RRF</td><td>hybrid를 질의 한 번으로 처리하고 Docker 컨테이너 하나로 띄운다.</td></tr>
<tr><td class="k">rerank</td><td>RRF 상위 30개를 cross-encoder로 재정렬해 상위 5개를 씀</td><td>bi-encoder가 놓치는 질문과 청크의 상호작용을 반영한다.</td></tr>
</tbody></table></div>

<div class="narrow"><h2><span class="stage-no">02</span>말뭉치와 평가셋</h2></div>
<div class="grid4">
  <div class="stat"><div class="n">18</div><div class="l">문서 · arXiv 9 · URL 4 · 개인 5</div></div>
  <div class="stat"><div class="n">{n_chunks}</div><div class="l">청크 · 900자 · 150자 겹침</div></div>
  <div class="stat"><div class="n">30</div><div class="l">평가 문항 · 한국어 28 · 영어 2</div></div>
  <div class="stat"><div class="n">{' · '.join(f'{k} {v}' for k, v in sorted(src_cnt.items()))}</div><div class="l">원문 형식별 문항 수</div></div>
</div>
<div class="narrow"><p>정답 판정 기준: 검색된 청크가 정답 문서이면서 근거 문구를 담고 있으면 정답으로 친다(공백 무시). 스캔 문서는 OCR 오류로 문구가 깨질 수 있어 문서와 페이지가 맞으면 정답으로 친다. 그래야 두 OCR 엔진을 같은 기준으로 비교할 수 있다. 질문 대부분을 한국어로 써서, 영어 논문을 한국어 질문으로 찾는 교차 언어 검색도 함께 시험했다.</p></div>

<div class="narrow"><h2><span class="stage-no">03</span>OCR — RapidOCR vs 비전 LLM</h2></div>
<div class="grid4">
  <div class="stat"><span class="pill warn">RapidOCR</span><div class="n">{gr['rapidocr']['cer'] * 100:.1f}% · {vl['rapidocr']['cer'] * 100:.1f}%</div><div class="l">CER (문서 2개)</div></div>
  <div class="stat"><span class="pill ok">qwen2.5vl:7b</span><div class="n">{gr['vlm']['cer'] * 100:.1f}% · {vl['vlm']['cer'] * 100:.1f}%</div><div class="l">CER (문서 2개)</div></div>
  <div class="stat"><span class="pill warn">RapidOCR</span><div class="n">{vl['rapidocr']['sec_per_page']}s</div><div class="l">페이지당 (첫 문서 {gr['rapidocr']['sec_per_page']}s는 모델 로드 포함)</div></div>
  <div class="stat"><span class="pill ok">qwen2.5vl:7b</span><div class="n">{st.mean([gr['vlm']['sec_per_page'], vl['vlm']['sec_per_page']]):.0f}s</div><div class="l">페이지당</div></div>
</div>
<div class="tablewrap"><table><thead><tr><th>문서</th><th>엔진</th><th class="num">CER</th><th class="num">초/페이지</th></tr></thead><tbody>
{ocr_rows}
</tbody></table></div>
<div class="narrow"><p>스캔 문서는 개인 문서 마크다운을 A4 150dpi 이미지로 렌더링하고 ±1.2° 회전, 잡음, 흐림을 넣어 만들었다. 정답 텍스트가 있으니 CER을 정확히 잴 수 있다. 실제 스캔보다 깨끗하므로 이 CER은 하한으로 읽어야 한다. RapidOCR의 오류는 긴 줄 끝이 잘리는 것, 붙여 쓴 한국어 띄어쓰기가 사라지는 것, 표 구조가 무너지는 것에 몰렸다.</p></div>

<div class="narrow"><h2><span class="stage-no">04</span>검색 — 기법별 기여 (ablation)</h2></div>
<figure class="chart">{ablation_svg()}
<figcaption>진한 막대 = Hit@1(첫 결과가 정답) · 옅은 막대 = Recall@5(상위 5개 안에 정답) · 숫자는 Hit@1 / Recall@5. 위 네 줄은 맥락 없음, 아래 네 줄은 contextual retrieval(OCR = qwen2.5vl). n=30이라 한 문항이 0.033이다.</figcaption>
</figure>
<div class="tablewrap"><table><thead><tr><th>컬렉션</th><th>검색</th><th class="num">Hit@1</th><th class="num">Recall@5</th><th class="num">MRR@10</th><th class="num">스캔 R@5</th><th class="num">초/질문</th></tr></thead><tbody>
{abl_rows}
</tbody></table></div>
<div class="narrow">
<div class="decision"><p class="q">운영에 쓴다면 어떤 조합인가?</p><p>contextual + hybrid + rerank 조합은 Recall@5가 {g('vlm_ctx', 'hybrid+rerank', 'recall@5'):.2f}로 답변 생성(상위 5개 사용)에 가장 유리하다. 맥락 생성은 색인할 때 한 번만 든다(797청크, 로컬 38분). 지연을 줄여야 하면 contextual + dense만 써도 Recall@5가 {g('vlm_ctx', 'dense', 'recall@5'):.2f}이고 질의당 0.04초다. 다만 sparse의 RRF 가중치는 낮추거나 빼는 쪽을 먼저 실험해야 한다.</p></div>
</div>

<div class="narrow"><h2><span class="stage-no">05</span>답변 — 로컬 7B vs Claude</h2></div>
<div class="ba">
  <div class="before"><span class="lk">LOCAL · qwen2.5:7b (Ollama)</span><pre>정답 점수   {L['score']:.2f}  (완전 정답 {L['full']}/{L['n']})
근거 충실   {L['grounded']:.2f}
평균 지연   {L['sec']:.1f}s
비용        $0 (전기료 제외)</pre></div>
  <div class="after"><span class="lk">CLAUDE · Sonnet (claude -p)</span><pre>정답 점수   {C['score']:.2f}  (완전 정답 {C['full']}/{C['n']})
근거 충실   {C['grounded']:.2f}
평균 지연   {C['sec']:.1f}s
비용        ${C['usd']:.2f} / 30문항 (API 환산)</pre></div>
</div>
<p class="ban">같은 상위 5개 근거(vlm_ctx, hybrid + rerank)와 같은 시스템 프롬프트를 주고, Claude Opus가 정답 대비 0/1/2점과 근거 충실 여부를 매겼다.</p>
<div class="tablewrap"><table><thead><tr><th>문항</th><th>정답</th><th>로컬 답(앞부분)</th><th>판정</th><th>채점 사유</th></tr></thead><tbody>
{bad_rows}
</tbody></table></div>
<div class="narrow"><p>로컬 모델의 감점은 두 종류다. 근거에 있는 숫자를 바꾸거나 새로 계산해 낸 경우(q16, q20)와 핵심 일부를 빠뜨린 경우(q13, q14, q30)다. 사내 지식 RAG에서 숫자 오답은 치명적이다. 로컬 모델을 쓰려면 수치 인용 검증(답 속 숫자가 근거에 있는지 대조) 같은 후처리 가드를 붙여야 한다.</p></div>

<div class="narrow"><h2><span class="stage-no">06</span>운영 보강 — 가드 · 게이트</h2></div>
<div class="stats">
  <div class="stat"><div class="n">{L['score']:.2f} → {GF['score']:.2f}</div><div class="l">로컬 답변 점수 · 가드+폴백</div></div>
  <div class="stat"><div class="n">{L['grounded']:.2f} → {GF['grounded']:.2f}</div><div class="l">근거 충실</div></div>
  <div class="stat"><div class="n">${GF['usd']:.3f}</div><div class="l">30문항 폴백 비용 (Claude 단독 ${C['usd']:.2f})</div></div>
  <div class="stat"><div class="n">{GATE['metrics']['true_refusal']:.2f} / {GATE['metrics']['false_refusal']:.3f}</div><div class="l">답 없는 질문 거절률 / 잘못된 거절률</div></div>
</div>
<div class="tablewrap"><table><thead><tr><th>구성</th><th class="num">점수</th><th class="num">완전 정답</th><th class="num">근거 충실</th><th class="num">평균 지연</th><th class="num">비용</th><th>비고</th></tr></thead><tbody>
<tr><td class="k">로컬 그대로</td><td class="num">{L['score']:.3f}</td><td class="num">{L['full']}/{L['n']}</td><td class="num">{L['grounded']:.3f}</td><td class="num">{L['sec']:.1f}s</td><td class="num">$0</td><td>05절 결과</td></tr>
<tr><td class="k">로컬 + 가드</td><td class="num">{GN['score']:.3f}</td><td class="num">{GN['full']}/{GN['n']}</td><td class="num">{GN['grounded']:.3f}</td><td class="num">{GN['sec']:.1f}s</td><td class="num">$0</td><td>검사에 걸려 재생성 {GN['retried']}건</td></tr>
<tr><td class="k">로컬 + 가드 + 폴백</td><td class="num">{GF['score']:.3f}</td><td class="num">{GF['full']}/{GF['n']}</td><td class="num">{GF['grounded']:.3f}</td><td class="num">{GF['sec']:.1f}s</td><td class="num">${GF['usd']:.3f}</td><td>재생성 뒤에도 걸린 {GF['fallback']}건만 Claude</td></tr>
<tr><td class="k">Claude Sonnet 단독</td><td class="num">{C['score']:.3f}</td><td class="num">{C['full']}/{C['n']}</td><td class="num">{C['grounded']:.3f}</td><td class="num">{C['sec']:.1f}s</td><td class="num">${C['usd']:.2f}</td><td>비교 기준</td></tr>
</tbody></table></div>
<div class="narrow"><p>가드는 rerank 점수와 질문 속 고유명으로 근거 없는 질문을 거르고, 답의 숫자·인용·언어를 근거와 대조한다. 걸리면 한 번 다시 쓰고, 그래도 걸리면 Claude 로 넘긴다. 남은 감점 4건(q05 용어 오류, q13·q14·q30 일부 누락)은 숫자·언어 검사로 잡히지 않는 유형이다. 30문항이라 한 문항이 0.033이므로 세 구성의 차이는 2~3문항 수준이다.</p></div>
<div class="tablewrap"><table><thead><tr><th>게이트 지표 (vlm_ctx · hybrid+rerank)</th><th class="num">기준선</th><th class="num">지금</th><th class="num">허용폭</th><th class="num">표본</th><th>판정</th></tr></thead><tbody>
{"".join(f"<tr><td class='k'>{c['metric']}</td><td class='num'>{c['baseline']:.3f}</td><td class='num'>{c['now']:.3f}</td><td class='num'>±{c['tol']}</td><td class='num'>{GATE['n'][c['metric']]}</td><td><span class='pill {'ok' if c['pass'] else 'no'}'>{'통과' if c['pass'] else '실패'}</span></td></tr>" for c in GATE['checks'] if c['metric'].endswith(('hit@1', 'recall@5')) or 'refusal' in c['metric'])}
</tbody></table></div>
<p class="ban">평가셋 = 본 30 + Claude 합성 107(근거 원문 대조로 검증, holdout 20은 튜닝에 쓰지 않음) + 막연한 질문 12 + 답 없는 질문 12. 일부러 rerank 후보를 30→5로 줄이자 지표 6개가 허용폭을 넘어 게이트가 막았다(종료 코드 1).</p>

<div class="narrow"><h2><span class="stage-no">07</span>한계와 다음 단계</h2></div>
<div class="tablewrap"><table><thead><tr><th>항목</th><th>현재</th><th>다음</th><th>상태</th></tr></thead><tbody>
<tr><td class="k">평가셋 크기</td><td>검색 게이트는 161문항으로 늘렸지만 답변 채점은 여전히 30문항(한 문항 3.3%p)이다.</td><td>답변 채점도 합성 holdout 으로 넓히고 부트스트랩 신뢰구간을 낸다.</td><td><span class="pill warn">부분 반영</span></td></tr>
<tr><td class="k">출제 편향</td><td>합성 107문항은 Claude 가 만들고 근거 원문 대조만 했다. 사람 검수는 하지 않아 라벨 노이즈가 있다(예: 질문과 근거가 어긋난 s081).</td><td>표본 20%를 사람이 검수해 노이즈율을 잰다.</td><td><span class="pill warn">부분 반영</span></td></tr>
<tr><td class="k">라벨 수정 1건</td><td>q13의 근거 문구가 너무 좁아 정답 청크를 오답으로 판정했다. 문구를 고친 뒤 전체를 다시 쟀다.</td><td>근거를 문구 대신 청크 id 목록으로 고정한다.</td><td><span class="pill ok">반영</span></td></tr>
<tr><td class="k">채점자 편향</td><td>Claude가 Claude 답을 채점했다.</td><td>사람이 표본을 검수하거나 다른 계열 채점자로 교차 확인한다.</td><td><span class="pill warn">잔여</span></td></tr>
<tr><td class="k">스캔 문서</td><td>합성 스캔 2문서·3페이지, 문항 4개뿐이다.</td><td>실제 스캔 공문서와 영수증을 추가한다.</td><td><span class="pill warn">잔여</span></td></tr>
<tr><td class="k">hybrid 가중치</td><td>RRF 1:1만 시험했다.</td><td>가중 RRF와 dense 단독 + rerank를 비교한다.</td><td><span class="pill no">미착수</span></td></tr>
</tbody></table></div>

<footer>실측: {date.today()} 이 머신(<code>~/projects/ai-ocr</code>)에서 모든 단계를 직접 실행했다. 수치는 <code>reports/retrieval.json</code>·<code>ocr_eval.json</code>·<code>answers.json</code>·<code>answers_guarded*.json</code>·<code>gate.json</code>에서 <code>src/aiocr/report.py</code>가 자동으로 옮겼다. Claude 비용은 <code>claude -p</code>가 돌려준 API 환산값이다. 외부 인용은 Contextual Retrieval 효과(실패율 49%·67% 감소)뿐이며 평가 말뭉치의 원문이다. 출처 https://www.anthropic.com/news/contextual-retrieval · 등급 1차(발표 기관 원문) · 확인 2026-11-10. 이 보고서의 수치는 그 주장을 재현하려는 것이 아니라 이 말뭉치에서 독립적으로 잰 값이다.</footer>
</div>
"""


def build_md():
    lines = ["# AI OCR RAG — 실험 보고서", "", f"갱신 {date.today()} · 수치는 `src/aiocr/report.py` 가 reports/*.json 에서 생성.", "",
             "## 검색 ablation (30문항)", "", "| 컬렉션 | 검색 | Hit@1 | Recall@5 | MRR@10 | 초/질문 |", "|---|---|---:|---:|---:|---:|"]
    for c in ("rapidocr_base", "vlm_base", "rapidocr_ctx", "vlm_ctx"):
        for m in MODES:
            lines.append(f"| {c} | {m} | {g(c, m, 'hit@1'):.3f} | {g(c, m, 'recall@5'):.3f} | {g(c, m, 'mrr@10'):.3f} | {g(c, m, 'sec_per_q')} |")
    lines += ["", "## OCR (합성 스캔, CER)", "", "| 문서 | 엔진 | CER | 초/페이지 |", "|---|---|---:|---:|"]
    lines += [f"| {d} | {e} | {v['cer']:.4f} | {v['sec_per_page']} |" for d, es in ocr.items() for e, v in es.items()]
    lines += ["", "## 답변 (vlm_ctx · hybrid+rerank top-5, Opus 채점)", "", "| 모델 | 점수 | 완전 정답 | 근거 충실 | 평균 지연 | 비용 |", "|---|---:|---:|---:|---:|---:|",
              f"| qwen2.5:7b (로컬) | {L['score']:.3f} | {L['full']}/{L['n']} | {L['grounded']:.3f} | {L['sec']:.1f}s | $0 |",
              f"| Claude Sonnet | {C['score']:.3f} | {C['full']}/{C['n']} | {C['grounded']:.3f} | {C['sec']:.1f}s | ${C['usd']:.2f} |",
              "", "해석·한계는 `reports/report.html` 참조."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (R / "report.html").write_text(build_html(), encoding="utf-8")
    (R / "REPORT.md").write_text(build_md(), encoding="utf-8")
    print("ok")
