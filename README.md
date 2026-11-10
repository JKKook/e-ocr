# AI OCR RAG

스캔 PDF·이미지까지 읽는 문서 RAG 를 로컬에서 끝까지 만들고, **기법을 하나씩 켤 때마다 검색·답변 품질이 얼마나 바뀌는지** 평가셋으로 잰 프로젝트.

```
수집(llm-wiki raw) → 파싱(텍스트 레이어 · pptx · html · OCR) → 청킹 → 임베딩(bge-m3 dense+sparse, ONNX)
→ Qdrant → 검색(dense · sparse · hybrid RRF) → reranker(bge-reranker-v2-m3) → contextual retrieval
→ 답변(로컬 qwen2.5:7b vs Claude Sonnet) → 채점(Claude Opus) → 보고서
```

| 단계 | 선택 | 파일 |
|---|---|---|
| 수집 | URL·arXiv PDF·마크다운 → `wiki/raw/` 원문 보관, `wiki/index.md`·`log.md` (llm-wiki 패턴) | `src/aiocr/collect.py` |
| 파싱 | PyMuPDF 텍스트 레이어, python-pptx, trafilatura, 텍스트 없는 페이지만 OCR | `src/aiocr/parse.py` |
| OCR | RapidOCR(PP-OCRv5 한국어, ONNX) vs Ollama qwen2.5vl:7b | `parse.py`, `ocr_eval.py` |
| 청킹 | 줄 단위 누적 900자, 150자 겹침, (문서, 페이지) 메타 | `rag.py` |
| 임베딩 | bge-m3 ONNX 한 번 추론으로 dense 1024 + 학습형 sparse | `embed.py` |
| 벡터 DB | Qdrant (named vector dense + sparse), Docker | `docker-compose.yml` |
| 검색 | dense · sparse · hybrid(RRF) · hybrid+rerank(top30→5) | `rag.py` |
| Contextual | 청크마다 문서 속 맥락 1~2문장(qwen2.5:7b) 을 앞에 붙여 dense·sparse 모두 색인 | `contextualize.py` |
| 평가 | 30문항(pdf 15 · html 2 · md 6 · pptx 3 · scan 4, 한국어 28 · 영어 2), Hit@1 · Recall@5 · MRR@10 | `eval/questions.jsonl`, `evaluate.py` |
| 답변 | 같은 top-5 근거로 qwen2.5:7b vs Claude Sonnet, Opus 채점 | `answer.py` |

## 실행

```bash
docker compose up -d                       # Qdrant :6333
uv sync
uv run python -m aiocr.collect             # 원문 수집
uv run python -m aiocr.parse rapidocr      # 파싱 (OCR = RapidOCR)
uv run python -m aiocr.parse vlm           # 파싱 (OCR = qwen2.5vl:7b)
uv run python -m aiocr.ocr_eval            # OCR CER
uv run python -m aiocr.contextualize vlm   # 청크 맥락 생성
uv run python -m aiocr.rag index vlm base && uv run python -m aiocr.rag index vlm ctx
uv run python -m aiocr.evaluate vlm_base vlm_ctx
uv run python -m aiocr.answer generate vlm_ctx && uv run python -m aiocr.answer judge
```

## 데스크톱 앱 (Electron + Next.js)

```bash
cd desktop && npm install
npm run app        # next build(정적 export) → Electron 창. API 는 앱이 빈 포트·새 토큰으로 띄우고 종료 시 내린다
npm run app:dev    # next dev(:3000) + 따로 띄운 API(:8765, 토큰 없음)로 개발
```

| 모드 | 화면 | 하는 일 |
|---|---|---|
| 사용자(기본) | 시작 화면 | "무엇이 궁금하세요?" 입력창 하나 — 질문 · 링크 붙여 넣기(웹·PDF·유튜브) · 파일 끌어놓기 · [자료 추가] · [검색할 문서 고르기] 모달. 올린 자료가 준비되면 그 범위에서 바로 답한다. 최근 문서 카드(요약 · 묻기 · 열기) |
| | 대화 | 스트리밍 답변, `[n]` 인용 칩(같은 쪽 묶음·인용된 출처 먼저), 출처 패널(쪽 이미지·읽어 낸 글자·영상 구간), 후속 질문 재작성, 피드백. 대화는 여러 개 저장 |
| | 문서 읽기 | 문서 정보·쪽 목록 \| 원문(쪽 이미지 ↔ 읽어 낸 글자, 영상은 구간 대본) \| 이 문서와 대화. 인용을 누르면 원문 그 쪽으로 이동·강조, 원문에서 고른 구절을 붙여 질문 |
| | 문서함 | 목록 · 열기 · 앱에서 추가한 문서 삭제 |
| 관리자(토글) | 질의 trace · 품질 게이트 · 검색 비교 · 성능 보고서 · 평가 문항 | 단계별 p50/p95, 가드 결과, 기준선 대비 판정, 기법 ablation |

영상: 유튜브·Vimeo 주소면 yt-dlp 로 사람이 단 자막 → 자동 자막(ko→en) 순으로 쓰고, 없으면 오디오를 받아 faster-whisper(large-v3-turbo, CPU int8, HF 캐시만 사용)로 받아쓴다. 대본은 60초 구간 = 한 '쪽'.

## 정적 데모 (웹, 읽기 전용)

서버·모델 없이 브라우저에서 돌려 보는 데모. 실제 API 응답을 `TestClient` 로 떠서 `/demo/<경로 해시>.json` 으로 싣고, 화면은 `NEXT_PUBLIC_DEMO=1` 이면 그 파일을 읽는다(쓰기·새 질문은 막음, 녹화한 예시 질문 7개와 대표 문서 6개의 요약·숫자 정리만 동작).

```bash
uv run python scripts/demo_export.py          # demo/data/ — 공개·개인(pms-*) 문서만, 원문 PDF·쪽 이미지는 pms-* 만
cd desktop && npm run build:demo               # demo/site/ (정적) — 끝에 데스크톱용 out/ 을 다시 빌드
cd ../demo/site && vercel deploy --prod --yes  # https://e-ocr.vercel.app (Vercel 프로젝트 e-ocr)
```

## 보고서 모드 (2026-11-10 grill 합의)

읽기 화면의 [보고서] 탭(문서 1개) · 문서함에서 2~4개 체크 → [비교 보고서]. 원할 때 만들고 `data/reports/<문서>.json` 에 저장, 청크 지문이 바뀌면 '오래됨' + [다시 만들기].

| 절 | 방법 | 원문 연결 |
|---|---|---|
| 요지·요약 | 문서를 고르게 훑은 청크 10개 → LLM, 숫자·언어 가드 | 근거 번호 → 쪽 |
| 핵심 키워드 | kiwi 명사·영문 고유명 → 문서 대 말뭉치 c-TF-IDF 상위 15 | 쪽 버튼 → 낱말 형광펜 |
| 대비 키워드 | ① 사전 포함 로그오즈 비율 z 점수(Monroe 2008, 말뭉치 나머지 또는 고른 다른 문서 대비) ② 문서 안(비교 시 문서 간) 대립 개념 쌍 — LLM | 쪽 |
| topic | bge-m3 dense → PCA 10 → KMeans, 실루엣 점수 90% 안의 가장 작은 k(2~8, 청크 4개당 1개 이하), 슬라이더 · 이름은 LLM | 대표 청크 쪽 |
| 벡터 지도 | 말뭉치 전체 dense → PCA 50 → t-SNE 2D(말뭉치 지문별 캐시). 회색 = 다른 문서, 색 = 이 문서 topic(비교는 문서별), 주황 고리 = 대화에서 검색된 근거 | 점 → 쪽 |
| 색인 통계 | 청크 수·차원·sparse 비영 평균·어휘·청크 크기·색인·방법 | — |
| 문서 구조 | 원래 쪽 텍스트의 절 제목(번호·로마자·#) + LLM 한 줄 · 없으면 쪽(영상은 구간)별 한 줄 | 제목 → 쪽 |
| 수치 모음 | 단위 붙은 수치 문장 → LLM 뜻 · 원문에 없는 값·중복은 버림 | 수치 형광펜 |
| 용어집 · 읽기 질문 | 핵심 키워드 중 5~8개 풀이(근거 인용) · 질문 4개(누르면 대화로) | 쪽 / 대화 |

LLM 출력은 처음 온전한 JSON 만 받고(7B 가 JSON 뒤에 반복·다른 언어를 붙이는 경우), 깨진 배열은 온전한 객체만 건지며, 한자 섞인 항목은 버리고 '한국어로만' 붙여 한 번 더 시도한다. 통계 절은 몇 초, LLM 절은 끝나는 대로 채운다(CRNN 9쪽 로컬 약 60~75초, LangGraph 3쪽 약 35초 실측).

## 영상 대본 정책 — SSOT: [`policy/transcript.json`](policy/transcript.json)

영상 대본에서 **번역·요약해도 되는 부분과 원문 그대로 둘 부분**은 이 파일 하나가 정한다(2026-11-11 사용자 결정). 판별기(`ingest.label_transcript`) · 색인 표시(`ingest.windows`) · 대화 답변(`pipeline`) · 보고서(`docreport`) · 읽기 화면 범례가 모두 이 파일을 읽고, 규칙을 바꾸면 `version` 을 올려 영상 문서를 다시 색인한다(`reindex(…, relabel=True)`, 버전이 다르면 자동).

| 분류 | 단위 | 답변·요약에서 | 색인 표시 |
|---|---|---|---|
| 대화·설명 | 줄 | 요약·의역·번역 가능 | 그대로 |
| 가사 | 줄 | 원문 그대로 인용, 뜻은 인용 뒤에 | `♪ … ♪` |
| 외국어 원문 발화 | 줄 | 원문 + (번역) 병기 | `〔…〕` |
| 효과·음악 표시 | 줄 | 내용 아님(키워드·검색 제외) | 빠짐 |
| 고유명·상표 | 낱말 | 표기 유지 | 그대로 |
| 자막 오류 의심 | 낱말 | 원문 유지 + 추정 병기 | `[?→추정]` |

판별은 로컬 LLM 이 기본값이 아닌 줄만 JSON 으로 돌려주는 방식이라 줄 내용을 바꾸지 않는다(원문에 없는 고유명·오류 원문은 버림, 외국어는 글자 비율로도 잡음). 읽기 화면에서 줄마다 분류를 고칠 수 있고, 고친 줄(overrides)은 다시 판별해도 유지된다.

## 운영 보강 (프로덕션형)

| 영역 | 구현 | 파일 |
|---|---|---|
| 근거 점수 게이트 | rerank top-1 ≥ 0.6 확신 높음 · 그 아래는 질문의 영문 고유명이 근거에 다 나오면 확신 낮음, 없으면 거절 · 고유명 없는 질문은 0.3 미만 거절. 보정 54문항(본 30·막연 12·답 없음 12) | `guard.py` |
| 답변 사후 검사 | 한자 섞임·한글 없음, 없는 인용 번호, 근거에 없는 숫자(만·억·k·million 환산) → 1회 재생성 → 그래도 걸리면 Claude 폴백 | `guard.py` `pipeline.py` |
| 보안 | URL 수집 SSRF 차단(사설·루프백·링크로컬·리다이렉트 재검사), 30MB 상한, API 토큰(실행마다 새로), CORS 로컬만, 기본 문서 삭제 금지 | `guard.py` `api.py` |
| 관측 | 질의마다 단계별 ms·토큰·비용·가드 결과·피드백을 SQLite(`data/traces.db`) | `trace.py` |
| 평가·게이트 | 합성 QA 107(Claude 생성, 근거 원문 대조로 검증, holdout 20) + 본 30 + 막연 12 + 답 없음 12. 기준선 대비 허용폭 max(0.02, 1/n) 넘으면 종료 코드 1 | `synth.py` `gate.py` |
| 개념 채점표 | RAG·OCR·CER·청킹·dense/sparse·RRF·reranker·contextual·지표·ablation 10개념 × (구현 4·측정 4·한계 2). 점수·감점 사유는 `eval/scorecard.json`, 근거 수치는 `reports/*.json` 에서 매번 다시 읽어 관리자 「성능 보고서」 맨 위에 표시. 근거 값이 채점 때와 달라지거나 기다리던 실험 결과(`next.expects`)가 생기면 「재채점」 표시 → 점수 고친 뒤 `python -m aiocr.scorecard --snapshot "<메모>"` 로 이력 한 줄 | `scorecard.py` |

```bash
uv run python -m aiocr.synth 8            # 합성 평가셋 (eval/synth.jsonl)
uv run python -m aiocr.gate --baseline    # 기준선 저장
uv run python -m aiocr.gate               # 판정 — 실패 시 exit 1 (CI·배포 전 단계에 건다)
AIOCR_FALLBACK=0 uv run python -m aiocr.answer guarded guarded_nofb   # 서비스 경로 답변 30문항 채점
```

### 아직 설계만 한 것 (다음 단계)

| 영역 | 지금 | 바꿀 방향 |
|---|---|---|
| 문서 수명주기 | 추가·삭제만, 갱신 없음 · 기본 청크 정수 id 와 추가 청크 UUID 혼재 · 전체 재색인은 색인을 지웠다 다시 만듦 | 원문 해시로 변경 감지·증분 갱신, id 를 UUID5(문서·청크) 로 통일, 새 컬렉션을 다 만든 뒤 alias 전환 |
| 서빙 구조 | 수집 작업 상태가 API 메모리(재시작 시 소실) · 모델이 API 프로세스 안 · rerank 질의당 1.5~2초(CPU) | Redis 큐 + 수집 워커, 임베딩·rerank 별도 추론 서버(GPU·배치), 질의 결과 캐시 |
| 파싱 품질 | PyMuPDF 평문 — 표·다단 깨짐 · 웹은 주소 한 장만(하위 페이지 안 따라감) · 자바스크립트로 그리는 페이지는 본문 일부만 | 레이아웃 인식 파싱(Docling 등), 제목 단위 청킹·표 통째 청크, 같은 사이트 링크 n 단계 수집·헤드리스 렌더 |
| 검색 튜닝 | sparse 섞은 hybrid 가 dense 를 못 넘음 · RRF 가중치 미실험 | 가중 RRF·질의 확장 실험을 게이트로 판정 |
| 접근 권한 | 한 사람이 쓰는 데스크톱 전제 | 문서별 ACL 을 payload 필터로, 감사 로그 |

모델: `models/bge-m3-onnx`(bge-m3 ONNX + sparse 머리 .npy), `models/bge-reranker-v2-m3`(HF), Ollama `qwen2.5:7b`·`qwen2.5vl:7b`.

결과와 해석은 [`reports/REPORT.md`](reports/REPORT.md).
