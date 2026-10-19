import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, type AskResponse } from "../lib/api";
import { Icon } from "./Icon";
type Props = { teamId: string; knowledgeBaseId: string; canAsk: boolean; processing: boolean; onError: (message: string) => void; };
type Turn = { id: number; question: string; result: AskResponse | null; error?: string; };
const suggestions = ["概括资料的主要内容", "列出资料中的关键要点"];
export function AskPanel({ teamId, knowledgeBaseId, canAsk, processing, onError }: Props) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const counter = useRef(0);
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const list = listRef.current;
    const latest = list?.lastElementChild as HTMLElement | null;
    if (list && latest) list.scrollTo({ top: latest.offsetTop - list.offsetTop, behavior: "auto" });
  }, [turns]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy || !canAsk) return;
    const id = ++counter.current;
    setTurns((prev) => [...prev, { id, question: text, result: null }]);
    setQuestion(""); setBusy(true);
    try { const result = await api.ask(teamId, knowledgeBaseId, text); setTurns((prev) => prev.map((turn) => turn.id === id ? { ...turn, result } : turn)); }
    catch (err) { const error = err instanceof ApiError ? err.message : "提问失败，请重试"; setTurns((prev) => prev.map((turn) => turn.id === id ? { ...turn, error } : turn)); setQuestion(text); onError(error); }
    finally { setBusy(false); inputRef.current?.focus(); }
  }
  return <section className="ask-panel" aria-labelledby="ask-heading">
    <div className="panel__head"><div className="panel__heading"><Icon name="book" size={19} /><h2 id="ask-heading">资料问答</h2></div><span className="panel__subtitle">回答附原文依据</span></div>
    {turns.length === 0 ? <div className="ask-empty"><div className="evidence-mark"><Icon name="quote" size={30} /></div><h3>{canAsk ? "从一个问题开始" : processing ? "资料正在整理中" : "你的资料，值得多问一句"}</h3><p>{canAsk ? "围绕当前知识库提问，\n答案与引用原文会一起呈现。" : processing ? "解析完成后，即可从文档中寻找答案。" : "上传团队资料，让分散的信息成为可查阅、\n可提问、可核对的知识。"}</p><div className="ask-suggestions">{suggestions.map((text) => <button key={text} disabled={!canAsk} onClick={() => { setQuestion(text); inputRef.current?.focus(); }}><Icon name="file" size={16} />{text}</button>)}</div></div> : <ol className="turn-list" ref={listRef}>{turns.map((turn) => <li key={turn.id} className="turn"><p className="turn__question">{turn.question}</p><div className="turn__body"><span className="answer-mark"><Icon name="book" size={18} /></span><div className="turn__content">{turn.error ? <p className="error-text" role="alert">{turn.error}，问题已保留在输入框中。</p> : turn.result === null ? <p className="turn__pending" role="status">正在检索资料并生成回答…</p> : <><p className="turn__answer">{turn.result.answer}</p>{turn.result.citations.length > 0 ? <div className="answer-sources"><h3><Icon name="quote" size={15} />原文依据 <span>{turn.result.citations.length}</span></h3><ol className="citation-list">{turn.result.citations.map((citation, index) => <li key={`${turn.id}-${index}`}><details className="citation" open><summary><span className="citation__number">{index + 1}</span><span className="citation__source">{citation.document_title}<span>{citation.locator_label}</span></span><Icon name="chevron" size={16} /></summary><blockquote>{citation.excerpt}</blockquote></details></li>)}</ol></div> : <p className="turn__insufficient">没有找到可引用的资料，请补充相关文档或调整问题。</p>}</>}</div></div></li>)}</ol>}
    <form className="ask-form" onSubmit={handleSubmit} aria-busy={busy}><label htmlFor="ask-input" className="sr-only">向当前知识库提问</label><div className="ask-form__composer"><textarea ref={inputRef} id="ask-input" rows={2} maxLength={2000} value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (!e.nativeEvent.isComposing && (e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} placeholder={canAsk ? "向资料提问…" : processing ? "解析完成后即可提问" : "先上传资料，再开始提问"} disabled={!canAsk} aria-describedby="ask-hint" /><div className="ask-form__bottom"><span id="ask-hint">{busy ? "正在生成回答…" : "⌘ / Ctrl + Enter 发送"}</span><button className="btn" type="submit" disabled={!canAsk || busy || !question.trim()}><Icon name="send" size={16} />{busy ? "回答中…" : "发送"}</button></div></div></form>
  </section>;
}
