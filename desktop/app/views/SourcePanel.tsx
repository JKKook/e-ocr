"use client";
import { useEffect, useState } from "react";
import { pageImage, pageText, type Doc, type Hit } from "../lib/api";
import { Icon } from "./icons";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const at = (url: string, s: number) => `${url}${url.includes("?") ? "&" : "?"}t=${Math.floor(s)}s`;

/** 출처 패널(부모가 key=청크 id 로 다시 그린다) — 원본 쪽 이미지(PDF·사진) 또는 영상 구간 대본과, 답에 쓰인 부분 강조. */
export function SourcePanel({ hit, doc, onClose, onOpenDoc }: { hit: Hit; doc?: Doc; onClose: () => void; onOpenDoc?: () => void }) {
  const [page, setPage] = useState<{ text: string; via: string; image: boolean; t0?: number | null } | null>(null);
  const [tab, setTab] = useState<"image" | "text">("image");
  const video = hit.via.startsWith("transcript");

  useEffect(() => {
    let live = true;
    pageText(hit.doc, hit.page).then((p) => { if (live) { setPage(p); setTab(p.image ? "image" : "text"); } }).catch(() => {});
    return () => { live = false; };
  }, [hit.doc, hit.page]);

  const text = page?.text ?? hit.text;
  const i = text.indexOf(hit.text.slice(0, 60));
  const span = i >= 0 ? [text.slice(0, i), text.slice(i, i + hit.text.length), text.slice(i + hit.text.length)] : null;
  const t0 = page?.t0 ?? hit.t0 ?? 0;

  return (
    <aside className="srcpanel" aria-label="출처" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <div className="head">
        <div style={{ minWidth: 0 }}>
          <b>{hit.title}</b>
          <div className="muted" style={{ fontSize: 12 }}>
            {video ? `영상 ${mmss(t0)}–${mmss(t0 + 60)} · ${hit.via === "transcript_stt" ? "음성을 받아 적은 대본" : "영상 자막"}`
              : `${hit.page}쪽 · ${page?.via.startsWith("ocr") ? "스캔 이미지를 OCR 로 읽음" : "문서 텍스트"}`}
          </div>
        </div>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          {onOpenDoc && <button className="ibtn" onClick={onOpenDoc}><Icon name="file" size={15} /><span>문서 전체 보기</span></button>}
          <button className="ibtn x" onClick={onClose} aria-label="출처 닫기"><Icon name="x" /></button>
        </div>
      </div>
      {video && doc?.url && (
        <a className="btn primary" style={{ margin: "10px 0", alignSelf: "flex-start", textDecoration: "none" }} href={at(doc.url, t0)} target="_blank" rel="noreferrer">
          <Icon name="play" size={15} /> 영상에서 {mmss(t0)}부터 보기
        </a>
      )}
      {page?.image && (
        <div className="seg" style={{ margin: "10px 0" }} role="group" aria-label="보기 방식">
          <button className={tab === "image" ? "on" : ""} onClick={() => setTab("image")}>원본 쪽</button>
          <button className={tab === "text" ? "on" : ""} onClick={() => setTab("text")}>읽어 낸 글자</button>
        </div>
      )}
      <div className="body">
        {page?.image && tab === "image" ? (
          // eslint-disable-next-line @next/next/no-img-element -- 로컬 API 가 그린 쪽 이미지(정적 export 라 next/image 최적화 불가)
          <img src={pageImage(hit.doc, hit.page)} alt={`${hit.title} ${hit.page}쪽`} />
        ) : (
          <div className="pagetext">{span ? <>{span[0]}<mark>{span[1]}</mark>{span[2]}</> : text}</div>
        )}
      </div>
    </aside>
  );
}
