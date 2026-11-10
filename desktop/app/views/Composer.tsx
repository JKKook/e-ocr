"use client";
import { useRef, useState } from "react";
import type { Doc, Job } from "../lib/api";
import type { Quote } from "./ChatView";
import { Icon, docIcon } from "./icons";

export type Attach = { key: string; label: string; kind: "file" | "url"; job?: Job; error?: string };
const URL_RE = /https?:\/\/[^\s<>"']+/g;
export const urlsIn = (t: string) => [...new Set(t.match(URL_RE) ?? [])];
export const stripUrls = (t: string) => t.replace(URL_RE, " ").replace(/\s+/g, " ").trim();

// 수집 단계(개발 이름)를 사용자 말로
export function jobStatus(j?: Job): string {
  if (!j) return "올리는 중";
  if (j.state === "ok") return j.result?.existing ? "이미 있는 자료" : "준비됨";
  if (j.state === "error") return "추가 못 함";
  const m = j.log.at(-1)?.msg ?? "";
  if (/받아쓰기 \d/.test(m)) return m.replace("받아쓰기", "받아쓰는 중");
  return { 대기: "차례 기다리는 중", 수집: "가져오는 중", 파싱: "글자 읽는 중", 맥락: "내용 정리 중", 색인: "저장하는 중" }[j.stage] ?? "처리 중";
}

type Props = {
  big?: boolean; busy: boolean; llm: "local" | "claude"; setLlm: (v: "local" | "claude") => void;
  attaches: Attach[]; onAttachUrl: (url: string) => void; onAttachFiles: (fs: File[]) => void; onRemoveAttach: (key: string) => void;
  scope: string[]; docs: Doc[]; onPickScope: () => void; onClearScope: () => void;
  onSend: (text: string) => void; inputRef?: React.RefObject<HTMLTextAreaElement | null>;
  fixed?: Doc; quote?: Quote | null; onClearQuote?: () => void;  // 읽기 화면: 범위 고정 · 원문 인용
  onOpenAttach?: (docId: string) => void;
};

/** 입력창 하나에서 질문·자료 추가·검색 범위를 다 한다. 링크를 붙여 넣으면 바로 자료로 올린다. */
export function Composer({ inputRef, ...p }: Props) {
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(false);
  const [linkMode, setLinkMode] = useState(false);
  const [link, setLink] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const pending = p.attaches.some((a) => !a.error && a.job?.state !== "ok");
  const canSend = !p.busy && (q.trim().length > 0 || p.attaches.length > 0 || !!p.quote);
  const scopeDocs = p.docs.filter((d) => p.scope.includes(d.id));

  function send() {
    if (!canSend) return;
    p.onSend(q);
    setQ("");
  }
  function addLink() {
    const us = urlsIn(link);
    us.forEach(p.onAttachUrl);
    if (us.length) { setLink(""); setLinkMode(false); }
  }

  return (
    <div className={`composer2 ${p.big ? "big" : ""}`}>
      {p.attaches.length > 0 && (
        <div className="attaches" aria-live="polite">
          {p.attaches.map((a) => (
            <span key={a.key} className={`att ${a.error || a.job?.state === "error" ? "bad" : a.job?.state === "ok" ? "ok" : ""}`}>
              <Icon name={a.kind === "file" ? "clip" : "link"} size={14} />
              <span className="t" title={a.label}>{a.job?.result?.title ?? a.label}</span>
              <span className="s">{a.error ?? (a.job?.state === "error" ? a.job.error : jobStatus(a.job))}</span>
              {(!a.job || a.job.state === "running" || a.job.state === "queued") && !a.error && <span className="spin" aria-hidden="true" />}
              {a.job?.state === "ok" && p.onOpenAttach && <button type="button" className="open" onClick={() => p.onOpenAttach!(a.job!.result!.id)}>열기</button>}
              <button type="button" aria-label={`${a.label} 빼기`} onClick={() => p.onRemoveAttach(a.key)}><Icon name="x" size={12} /></button>
            </span>
          ))}
        </div>
      )}
      {p.quote && (
        <div className="quotebox">
          <span className="ql">원문 {p.quote.label}</span>
          <span className="qt">{p.quote.text.length > 180 ? p.quote.text.slice(0, 180) + "…" : p.quote.text}</span>
          <button type="button" aria-label="인용 빼기" onClick={p.onClearQuote}><Icon name="x" size={12} /></button>
        </div>
      )}
      <textarea ref={inputRef} value={q} rows={p.big ? 3 : 1} aria-label="질문"
        placeholder={p.big ? "궁금한 것을 물어보세요. 웹·PDF·유튜브 링크를 붙여 넣으면 그 자료로 바로 답해요."
          : p.quote ? "이 부분에 대해 무엇이 궁금하세요?" : p.fixed ? "이 문서에 대해 물어보세요" : "이어서 물어보세요 (Enter 보내기 · Shift+Enter 줄바꿈)"}
        onChange={(e) => setQ(e.target.value)}
        onPaste={(e) => {
          const t = e.clipboardData.getData("text").trim();
          if (urlsIn(t).length === 1 && urlsIn(t)[0] === t) { e.preventDefault(); p.onAttachUrl(t); }  // 링크만 붙여 넣으면 자료로
        }}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />

      {linkMode && (
        <form className="linkrow" onSubmit={(e) => { e.preventDefault(); addLink(); }}>
          <Icon name="link" size={16} />
          <input autoFocus type="text" aria-label="자료 링크" value={link} onChange={(e) => setLink(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") setLinkMode(false); }}
            placeholder="https:// — 웹 문서 · PDF · 유튜브 영상" />
          <button className="btn" disabled={!urlsIn(link).length}>추가</button>
        </form>
      )}

      <div className="bar2">
        {!p.fixed && <div className="pop">
          <button type="button" className="ibtn" aria-label="자료 추가" aria-expanded={menu} onClick={() => setMenu(!menu)}>
            <Icon name="plus" /><span>자료 추가</span>
          </button>
          {menu && (
            <div className="menu" role="menu" onMouseLeave={() => setMenu(false)}>
              <button role="menuitem" onClick={() => { setMenu(false); file.current?.click(); }}><Icon name="clip" />파일 올리기<small>PDF·스캔·사진·PPTX·텍스트</small></button>
              <button role="menuitem" onClick={() => { setMenu(false); setLinkMode(true); }}><Icon name="link" />링크 넣기<small>웹 문서·PDF·유튜브 영상</small></button>
            </div>
          )}
          <input ref={file} type="file" hidden multiple accept=".pdf,.png,.jpg,.jpeg,.pptx,.md,.txt,.html,.htm"
            onChange={(e) => { p.onAttachFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        </div>}
        {!p.fixed && <button type="button" className={`ibtn ${p.scope.length ? "on" : ""}`} onClick={p.onPickScope}
          aria-label="검색할 문서 고르기" title={scopeDocs.map((d) => d.title).join("\n") || "모든 문서에서 찾아요"}>
          <Icon name="stack" />
          <span>{p.scope.length ? (scopeDocs.length === 1 ? scopeDocs[0].title : `문서 ${p.scope.length}개에서`) : `전체 문서 ${p.docs.length}개`}</span>
        </button>}
        {p.fixed && <span className="muted fixedscope"><Icon name="stack" size={15} />이 문서에서만</span>}
        {p.scope.length > 0 && !p.fixed && <button type="button" className="ibtn x" aria-label="범위 풀기(전체 문서)" onClick={p.onClearScope}><Icon name="x" size={14} /></button>}
        <span style={{ flex: 1 }} />
        <div className="seg" role="group" aria-label="답을 쓰는 모델">
          <button type="button" className={p.llm === "local" ? "on" : ""} onClick={() => p.setLlm("local")} title="문서가 밖으로 나가지 않아요">내 컴퓨터</button>
          <button type="button" className={p.llm === "claude" ? "on" : ""} onClick={() => p.setLlm("claude")} title="질문과 근거 일부가 Claude 로 전송돼요">Claude</button>
        </div>
        <button type="button" className="send" aria-label={pending ? "자료가 준비되면 보내기" : "보내기"} disabled={!canSend} onClick={send}>
          <Icon name="send" />
        </button>
      </div>
    </div>
  );
}

/** 문서함 모달 — 대화를 떠나지 않고 검색 범위를 고른다. */
export function DocPicker({ docs, scope, onApply, onClose }: { docs: Doc[]; scope: string[]; onApply: (ids: string[]) => void; onClose: () => void }) {
  const [sel, setSel] = useState<string[]>(scope);
  const [f, setF] = useState("");
  const list = [...docs].sort((a, b) => b.added - a.added).filter((d) => d.title.toLowerCase().includes(f.toLowerCase()));
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="검색할 문서 고르기" onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
        <div className="mhead">
          <b>어떤 문서에서 찾을까요?</b>
          <button className="ibtn x" aria-label="닫기" onClick={onClose}><Icon name="x" /></button>
        </div>
        <label className="searchbox"><Icon name="search" size={16} /><input autoFocus type="text" value={f} onChange={(e) => setF(e.target.value)} placeholder="문서 이름으로 찾기" aria-label="문서 이름으로 찾기" /></label>
        <div className="mlist">
          {list.map((d) => (
            <label key={d.id} className={`mitem ${sel.includes(d.id) ? "on" : ""}`}>
              <input type="checkbox" checked={sel.includes(d.id)} onChange={() => toggle(d.id)} />
              <Icon name={docIcon(d.type)} />
              <span className="t">{d.title}</span>
              <span className="muted">{d.group === "user" ? "내가 추가" : "기본"}</span>
            </label>
          ))}
        </div>
        <div className="mfoot">
          <button className="btn" onClick={() => onApply([])}>전체 문서에서 찾기</button>
          <span style={{ flex: 1 }} />
          <button className="btn primary" disabled={!sel.length} onClick={() => onApply(sel)}>{sel.length}개 문서에서 찾기</button>
        </div>
      </div>
    </div>
  );
}
