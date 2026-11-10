# AI OCR RAG — 실험 보고서

갱신 2026-11-10 · 수치는 `src/aiocr/report.py` 가 reports/*.json 에서 생성.

## 검색 ablation (30문항)

| 컬렉션 | 검색 | Hit@1 | Recall@5 | MRR@10 | 초/질문 |
|---|---|---:|---:|---:|---:|
| rapidocr_base | dense | 0.367 | 0.833 | 0.557 | 0.06 |
| rapidocr_base | sparse | 0.233 | 0.600 | 0.421 | 0.03 |
| rapidocr_base | hybrid | 0.400 | 0.767 | 0.558 | 0.03 |
| rapidocr_base | hybrid+rerank | 0.800 | 0.967 | 0.869 | 2.21 |
| vlm_base | dense | 0.400 | 0.867 | 0.591 | 0.04 |
| vlm_base | sparse | 0.233 | 0.633 | 0.422 | 0.04 |
| vlm_base | hybrid | 0.400 | 0.800 | 0.565 | 0.03 |
| vlm_base | hybrid+rerank | 0.800 | 0.967 | 0.869 | 2.07 |
| rapidocr_ctx | dense | 0.533 | 0.967 | 0.716 | 0.03 |
| rapidocr_ctx | sparse | 0.367 | 0.600 | 0.479 | 0.04 |
| rapidocr_ctx | hybrid | 0.533 | 0.833 | 0.674 | 0.03 |
| rapidocr_ctx | hybrid+rerank | 0.767 | 1.000 | 0.862 | 2.42 |
| vlm_ctx | dense | 0.533 | 0.967 | 0.711 | 0.04 |
| vlm_ctx | sparse | 0.367 | 0.633 | 0.492 | 0.04 |
| vlm_ctx | hybrid | 0.500 | 0.833 | 0.660 | 0.03 |
| vlm_ctx | hybrid+rerank | 0.767 | 1.000 | 0.862 | 2.37 |

## OCR (합성 스캔, CER)

| 문서 | 엔진 | CER | 초/페이지 |
|---|---|---:|---:|
| pms-gg-release | rapidocr | 0.1083 | 13.33 |
| pms-gg-release | vlm | 0.0167 | 36.05 |
| pms-ml-lab | rapidocr | 0.2148 | 0.52 |
| pms-ml-lab | vlm | 0.0000 | 34.86 |

## 답변 (vlm_ctx · hybrid+rerank top-5, Opus 채점)

| 모델 | 점수 | 완전 정답 | 근거 충실 | 평균 지연 | 비용 |
|---|---:|---:|---:|---:|---:|
| qwen2.5:7b (로컬) | 0.833 | 23/30 | 0.867 | 6.9s | $0 |
| Claude Sonnet | 1.000 | 30/30 | 1.000 | 3.7s | $0.36 |

해석·한계는 `reports/report.html` 참조.
