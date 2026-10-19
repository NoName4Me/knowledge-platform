import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type Conversation,
  type Turn,
} from "../lib/api";
import { Icon } from "./Icon";

type Props = {
  teamId: string;
  knowledgeBaseId: string;
  canAsk: boolean;
  processing: boolean;
  onError: (message: string) => void;
};

const suggestions = ["概括资料的主要内容", "列出资料中的关键要点"];

const turnError = (turn: Turn): string => {
  if (turn.error_code === "MODEL_UNAVAILABLE") return "回答服务暂不可用，请稍后重新提问。";
  return "本次回答未完成，请重新提问。";
};

export function AskPanel({
  teamId,
  knowledgeBaseId,
  canAsk,
  processing,
  onError,
}: Props) {
  const [question, setQuestion] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingTurns, setLoadingTurns] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloadTurns, setReloadTurns] = useState(0);
  const listRef = useRef<HTMLOListElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sendingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api
      .listConversations(teamId, knowledgeBaseId)
      .then((items) => {
        if (cancelled) return;
        setConversations(items);
        setActiveId(items[0]?.id ?? null);
      })
      .catch((error) => {
        if (!cancelled) {
          onError(error instanceof ApiError ? error.message : "加载问答历史失败，请重试");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingConversations(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, knowledgeBaseId, onError]);

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    const loadTurns = async () => {
      setLoadingTurns(true);
      try {
        const items = await api.listTurns(teamId, knowledgeBaseId, activeId);
        if (!cancelled) setTurns(items);
      } catch (error) {
        if (!cancelled) {
          onError(error instanceof ApiError ? error.message : "加载问答记录失败，请重试");
        }
      } finally {
        if (!cancelled) setLoadingTurns(false);
      }
    };
    void loadTurns();
    return () => {
      cancelled = true;
    };
  }, [teamId, knowledgeBaseId, activeId, reloadTurns, onError]);

  useEffect(() => {
    const list = listRef.current;
    const latest = list?.lastElementChild as HTMLElement | null;
    if (list && latest) {
      list.scrollTo({ top: latest.offsetTop - list.offsetTop, behavior: "auto" });
    }
  }, [turns]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || sendingRef.current || !canAsk) return;

    sendingRef.current = true;
    setQuestion("");
    setBusy(true);
    let conversationId = activeId;
    let requestId: string | null = null;
    try {
      if (!conversationId) {
        const created = await api.createConversation(
          teamId,
          knowledgeBaseId,
          text.slice(0, 100),
        );
        conversationId = created.id;
        setConversations((items) => [created, ...items]);
        setActiveId(created.id);
      }

      requestId = crypto.randomUUID();
      const existingTurnCount = activeId === conversationId ? turns.length : 0;
      const pending: Turn = {
        id: requestId,
        seq: existingTurnCount + 1,
        client_request_id: requestId,
        question: text,
        status: "answering",
        answer: null,
        error_code: null,
        started_at: new Date().toISOString(),
        finished_at: null,
        citations: [],
      };
      setTurns((items) => [...items, pending]);
      const completed = await api.askConversation(
        teamId,
        knowledgeBaseId,
        conversationId,
        requestId,
        text,
      );
      setTurns((items) => [...items.filter((item) => item.id !== requestId), completed]);
      setConversations((items) => [
        ...items
          .filter((item) => item.id === conversationId)
          .map((item) => ({ ...item, updated_at: new Date().toISOString() })),
        ...items.filter((item) => item.id !== conversationId),
      ]);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "提问失败，请重试";
      setTurns((items) => items.filter((item) => item.client_request_id !== requestId));
      setQuestion(text);
      onError(message);
      if (conversationId) setReloadTurns((revision) => revision + 1);
    } finally {
      sendingRef.current = false;
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  const activeConversation = conversations.find((item) => item.id === activeId);
  const canStartNewConversation = canAsk && !loadingConversations;

  return (
    <section className="ask-panel" aria-labelledby="ask-heading">
      <div className="panel__head">
        <div className="panel__heading">
          <Icon name="book" size={19} />
          <h2 id="ask-heading">资料问答</h2>
        </div>
        <span className="panel__subtitle">回答附原文依据</span>
      </div>
      <div className="conversation-bar">
        <label htmlFor="conversation-select" className="sr-only">选择会话</label>
        <Icon name="quote" size={15} />
        <select
          id="conversation-select"
          value={activeId ?? "draft"}
          onChange={(event) =>
            setActiveId(event.target.value === "draft" ? null : event.target.value)
          }
          disabled={busy || loadingConversations || loadingTurns}
        >
          {activeId === null && <option value="draft">新对话</option>}
          {conversations.map((conversation) => (
            <option key={conversation.id} value={conversation.id}>
              {conversation.title}
            </option>
          ))}
        </select>
        <button
          className="icon-button"
          type="button"
          onClick={() => {
            setActiveId(null);
            setTurns([]);
            inputRef.current?.focus();
          }}
          disabled={busy || !canStartNewConversation || (activeId === null && turns.length === 0)}
          aria-label="开始新对话"
          title="开始新对话"
        >
          <Icon name="plus" size={16} />
        </button>
        <span className="conversation-bar__privacy">
          <Icon name="shield" size={13} />
          仅本人可见
        </span>
      </div>

      {loadingConversations || (loadingTurns && turns.length === 0) ? (
        <p className="ask-history-state" role="status">正在加载问答记录…</p>
      ) : turns.length === 0 ? (
        <div className="ask-empty">
          <div className="evidence-mark"><Icon name="quote" size={30} /></div>
          <h3>
            {canAsk
              ? activeConversation
                ? "继续这次对话"
                : "从一个问题开始"
              : processing
                ? "资料正在整理中"
                : "你的资料，值得多问一句"}
          </h3>
          <p>
            {canAsk
              ? "围绕当前知识库提问，\n答案与引用原文会一起呈现。"
              : processing
                ? "解析完成后，即可从文档中寻找答案。"
                : "上传团队资料，让分散的信息成为可查阅、\n可提问、可核对的知识。"}
          </p>
          <div className="ask-suggestions">
            {suggestions.map((text) => (
              <button
                key={text}
                disabled={!canAsk}
                onClick={() => {
                  setQuestion(text);
                  inputRef.current?.focus();
                }}
              >
                <Icon name="file" size={16} />
                {text}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <ol className="turn-list" ref={listRef}>
          {turns.map((turn) => (
            <li key={turn.id} className="turn">
              <p className="turn__question">{turn.question}</p>
              <div className="turn__body">
                <span className="answer-mark"><Icon name="book" size={18} /></span>
                <div className="turn__content">
                  {turn.status === "answering" ? (
                    <p className="turn__pending" role="status">正在检索资料并生成回答…</p>
                  ) : turn.status === "failed" || turn.status === "interrupted" ? (
                    <div className="turn__failure">
                      <p className="error-text" role="alert">{turnError(turn)}</p>
                      <button
                        className="btn btn--ghost"
                        type="button"
                        disabled={!canAsk || busy}
                        onClick={() => {
                          setQuestion(turn.question);
                          inputRef.current?.focus();
                        }}
                      >
                        重新提问
                      </button>
                    </div>
                  ) : (
                    <>
                      <p className="turn__answer">{turn.answer}</p>
                      {turn.citations.length > 0 ? (
                        <div className="answer-sources">
                          <h3>
                            <Icon name="quote" size={15} />
                            原文依据 <span>{turn.citations.length}</span>
                          </h3>
                          <ol className="citation-list">
                            {turn.citations.map((citation, index) => (
                              <li key={`${turn.id}-${citation.document_id}-${index}`}>
                                <details className="citation" open>
                                  <summary>
                                    <span className="citation__number">{index + 1}</span>
                                    <span className="citation__source">
                                      {citation.document_title}
                                      <span>{citation.locator_label}</span>
                                    </span>
                                    <Icon name="chevron" size={16} />
                                  </summary>
                                  <blockquote>
                                    {citation.excerpt || "原始资料已删除，保留引用来源信息。"}
                                  </blockquote>
                                </details>
                              </li>
                            ))}
                          </ol>
                        </div>
                      ) : turn.status === "insufficient" ? (
                        <p className="turn__insufficient">
                          没有找到可引用的资料，请补充相关文档或调整问题。
                        </p>
                      ) : null}
                    </>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      <form className="ask-form" onSubmit={handleSubmit} aria-busy={busy}>
        <label htmlFor="ask-input" className="sr-only">向当前知识库提问</label>
        <div className="ask-form__composer">
          <textarea
            ref={inputRef}
            id="ask-input"
            rows={2}
            maxLength={2000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (
                !event.nativeEvent.isComposing
                && (event.ctrlKey || event.metaKey)
                && event.key === "Enter"
              ) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={
              canAsk
                ? "向资料提问…"
                : processing
                  ? "解析完成后即可提问"
                  : "先上传资料，再开始提问"
            }
            disabled={!canStartNewConversation || busy}
            aria-describedby="ask-hint"
          />
          <div className="ask-form__bottom">
            <span id="ask-hint">{busy ? "正在生成回答…" : "⌘ / Ctrl + Enter 发送"}</span>
            <button
              className="btn"
              type="submit"
              disabled={!canStartNewConversation || busy || !question.trim()}
            >
              <Icon name="send" size={16} />
              {busy ? "回答中…" : "发送"}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
