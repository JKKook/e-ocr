"""bge-m3 ONNX 임베더 — 한 번의 추론으로 dense(1024, L2 정규화)와 sparse(토큰별 학습 가중치)를 함께 낸다.

sparse = ReLU(token_embeddings · W_sparse + b), 같은 토큰은 최댓값, 특수 토큰은 제외(BGE-M3 논문의 lexical weight).
"""
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

M = Path(__file__).resolve().parents[2] / "models" / "bge-m3-onnx"
SPECIAL = {0, 1, 2, 3}  # <s> <pad> </s> <unk>


class BgeM3:
    def __init__(self, max_len: int = 1024):
        self.sess = ort.InferenceSession(str(M / "onnx" / "model.onnx"), providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(M / "tokenizer.json"))
        self.tok.enable_truncation(max_len)
        self.w = np.load(M / "sparse_linear.weight.npy")[0]  # (1024,)
        self.b = float(np.load(M / "sparse_linear.bias.npy")[0])

    def encode(self, texts: list[str], batch: int = 8) -> tuple[np.ndarray, list[dict[int, float]]]:
        dense, sparse = [], []
        for i in range(0, len(texts), batch):
            enc = self.tok.encode_batch(texts[i:i + batch])
            n = max(len(e.ids) for e in enc)
            ids = np.array([e.ids + [1] * (n - len(e.ids)) for e in enc], dtype=np.int64)
            mask = np.array([[1] * len(e.ids) + [0] * (n - len(e.ids)) for e in enc], dtype=np.int64)
            tok_emb, sent = self.sess.run(None, {"input_ids": ids, "attention_mask": mask})
            dense.append(sent / np.linalg.norm(sent, axis=1, keepdims=True))
            weights = np.maximum(tok_emb @ self.w + self.b, 0)
            for row_ids, row_w, row_m in zip(ids, weights, mask):
                d: dict[int, float] = {}
                for t, w, m in zip(row_ids, row_w, row_m):
                    if m and t not in SPECIAL and w > 0:
                        d[int(t)] = max(d.get(int(t), 0.0), float(w))
                sparse.append(d)
        return np.vstack(dense), sparse
