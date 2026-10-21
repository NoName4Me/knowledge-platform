import {
  startTransition,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  api,
  ApiError,
  type Conversation,
  type DocumentItem,
  type Turn,
} from "../lib/api";
import { AnswerWithEvidence, StreamingAnswer } from "./AnswerWithEvidence";
import { turnEvidence } from "../lib/citation-text";
import { documentLabel } from "../lib/document-label";
import { useEvidence } from "../lib/evidence-context";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { SelectMenu } from "./Popover";

type Props = {
  teamId: string;
  knowledgeBaseId: string;
  documents: DocumentItem[];
  canAsk: boolean;
  processing: boolean;
  onError: (message: string) => void;
};

const suggestions = ["概括资料的主要内容", "列出资料中的关键要点"];

const turnError = (turn: Turn): string => {
  if (turn.error_code === "MODEL_UNAVAILABLE") return "回答服务暂不可用，请稍后重新提问。";
  if (turn.error_code === "SOURCE_DELETED") return "引用来源已被删除，请重新提问。";
  if (turn.error_code === "ACCESS_REVOKED") return "当前访问权限已失效，请刷新后重试。";
  return "本次回答未完成，请重新提问。";
};

export function AskPanel({
  teamId,
  knowledgeBaseId,
  documents,
  canAsk,
  processing,
  onError,
}: Props) {
  const evidence = useEvidence();
  const [question, setQuestion] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingTurns, setLoadingTurns] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloadTurns, setReloadTurns] = useState(0);
  const [scopeMode, setScopeMode] = useState<"all" | "selected">("all");
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [noteTurn, setNoteTurn] = useState<Turn | null>(null);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteBody, setNoteBody] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const listRef = useRef<HTMLOListElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scopeTriggerRef = useRef<HTMLButtonElement>(null);
  const sendingRef = useRef(false);
  const readyDocuments = useMemo(
    () => documents.filter((document) => document.status === "ready"),
    [documents],
  );
  const readyDocumentIds = new Set(readyDocuments.map((document) => document.id));
  const validSelectedDocumentIds = selectedDocumentIds.filter((id) =>
    readyDocumentIds.has(id),
  );
  const selectedDocuments = readyDocuments.filter((document) =>
    validSelectedDocumentIds.includes(document.id),
  );
  const effectiveScopeMode =
    scopeMode === "selected" && selectedDocuments.length > 0 ? "selected" : "all";
  const documentIds =
    effectiveScopeMode === "selected" ? validSelectedDocumentIds : undefined;

  useEffect(() => {
    if (!scopeOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setScopeOpen(false);
      scopeTriggerRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [scopeOpen]);

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
    if (
      !text ||
      sendingRef.current ||
      !canAsk ||
      (scopeMode === "selected" && selectedDocuments.length === 0)
    ) return;

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
      const currentRequestId = requestId;
      startTransition(() => {
        setTurns((items) => {
          const existingTurnCount = activeId === conversationId ? items.length : 0;
          const pending: Turn = {
            id: currentRequestId,
            seq: existingTurnCount + 1,
            client_request_id: currentRequestId,
            question: text,
            scope_document_ids: documentIds ?? [],
            status: "answering",
            answer: null,
            answer_draft: null,
            error_code: null,
            started_at: new Date().toISOString(),
            finished_at: null,
            citations: [],
          };
          return [...items, pending];
        });
      });
      let streamedText = "";
      let completed: Turn | null = null;
      let streamError: ApiError | null = null;
      await api.streamConversationTurn(
        teamId,
        knowledgeBaseId,
        conversationId,
        currentRequestId,
        text,
        documentIds,
        (event) => {
          if (event.type === "delta" && event.text) {
            streamedText += event.text;
            setTurns((items) =>
              items.map((item) =>
                item.client_request_id === currentRequestId
                  ? { ...item, answer_draft: streamedText }
                  : item,
              ),
            );
          } else if (event.type === "turn" && event.turn) {
            completed = event.turn;
            setTurns((items) => [
              ...items.filter(
                (item) =>
                  item.id !== currentRequestId &&
                  item.client_request_id !== currentRequestId &&
                  item.id !== event.turn?.id,
              ),
              event.turn as Turn,
            ]);
          } else if (event.type === "error") {
            streamError = new ApiError(
              502,
              event.message ?? "回答服务暂不可用，请稍后重试。",
            );
          }
        },
      );
      if (streamError) {
        throw streamError;
      }
      if (!completed) {
        const refreshed = await api.listTurns(teamId, knowledgeBaseId, conversationId);
        setTurns(refreshed);
      }
      setConversations((items) => [
        ...items
          .filter((item) => item.id === conversationId)
          .map((item) => ({ ...item, updated_at: new Date().toISOString() })),
        ...items.filter((item) => item.id !== conversationId),
      ]);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "提问失败，请重试";
      setQuestion(text);
      onError(message);
      if (conversationId) setReloadTurns((revision) => revision + 1);
    } finally {
      sendingRef.current = false;
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function openNoteEditor(turn: Turn) {
    setNoteTurn(turn);
    setNoteTitle(turn.question.slice(0, 200));
    setNoteBody(turn.answer ?? "");
  }

  async function saveNote() {
    if (!noteTurn || !noteTitle.trim() || !noteBody.trim() || savingNote) return;
    setSavingNote(true);
    try {
      await api.createNote(teamId, knowledgeBaseId, {
        title: noteTitle.trim(),
        content: noteBody.trim(),
        source_type: "conversation",
        source_id: activeId,
        citations: noteTurn.citations.map((citation) => ({
          document_id: citation.document_id,
          document_title: citation.document_title,
          locator_label: citation.locator_label,
          excerpt: citation.excerpt,
        })),
      });
      setNoteTurn(null);
    } catch (error) {
      onError(error instanceof ApiError ? error.message : "保存问答笔记失败，请重试");
    } finally {
      setSavingNote(false);
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
        <Icon name="quote" size={15} />
        <SelectMenu
          className="conversation-select"
          label="选择会话"
          value={activeId ?? "draft"}
          onChange={(value) => {
            evidence.close();
            setActiveId(value === "draft" ? null : value);
            setTurns([]);
          }}
          disabled={busy || loadingConversations || loadingTurns}
          options={[
            ...(activeId === null ? [{ id: "draft", label: "新对话" }] : []),
            ...conversations.map((conversation) => ({
              id: conversation.id,
              label: conversation.title,
            })),
          ]}
        />
        <button
          className="icon-button"
          type="button"
          onClick={() => {
            evidence.close();
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
      </div>
      <div className="scope-bar">
        <button
          ref={scopeTriggerRef}
          type="button"
          className="scope-bar__trigger"
          aria-expanded={scopeOpen}
          aria-controls="ask-scope-panel"
          disabled={busy || readyDocuments.length === 0}
          onClick={() => setScopeOpen((value) => !value)}
        >
          <Icon name="file" size={15} />
          <span>
            {effectiveScopeMode === "all"
              ? `全部资料 · ${readyDocuments.length} 份`
              : `已选 ${selectedDocuments.length} 份资料`}
          </span>
          <Icon name="chevron" size={14} />
        </button>
        {scopeOpen ? (
          <div className="scope-panel" id="ask-scope-panel">
            <label className="scope-option">
              <input
                type="radio"
                name="ask-scope"
                checked={effectiveScopeMode === "all"}
                onChange={() => setScopeMode("all")}
              />
              <span>当前知识库全部资料</span>
            </label>
            <label className="scope-option">
              <input
                type="radio"
                name="ask-scope"
                checked={effectiveScopeMode === "selected"}
                onChange={() => {
                  setScopeMode("selected");
                  setSelectedDocumentIds((ids) =>
                    ids.length
                      ? ids
                      : readyDocuments.map((document) => document.id),
                  );
                }}
              />
              <span>仅指定资料</span>
            </label>
            <div
              className={`scope-documents${scopeMode === "selected" ? " is-active" : ""}`}
            >
              {readyDocuments.map((document) => (
                <label className="scope-option" key={document.id}>
                  <input
                    type="checkbox"
                    checked={validSelectedDocumentIds.includes(document.id)}
                    onChange={(event) =>
                      setSelectedDocumentIds((ids) =>
                        event.target.checked
                          ? [...ids, document.id]
                          : ids.filter((id) => id !== document.id),
                      )
                    }
                  />
                  <span>{documentLabel(document)}</span>
                </label>
              ))}
              {selectedDocuments.length === 0 ? (
                <p className="scope-hint">
                  取消全部选项后，本次提问会恢复为全部资料。
                </p>
              ) : null}
            </div>
            <div className="scope-panel__actions">
              <button
                className="btn btn--ghost"
                type="button"
                onClick={() => {
                  setScopeOpen(false);
                  scopeTriggerRef.current?.focus();
                }}
              >
                完成
              </button>
            </div>
          </div>
        ) : null}
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
                    turn.answer_draft ? (
                      <StreamingAnswer text={turn.answer_draft} />
                    ) : (
                      <p className="turn__pending" role="status">正在检索资料并生成回答…</p>
                    )
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
                      <AnswerWithEvidence turn={turn} loadSources={async () => {
                        if (!activeId) return [];
                        const currentTurns = await api.listTurns(teamId, knowledgeBaseId, activeId);
                        const current = currentTurns.find((item) => item.id === turn.id);
                        if (!current) throw new Error("这条回答已不可访问，请重新加载会话");
                        return turnEvidence(current);
                      }} />
                      {turn.answer && (
                        <button
                          type="button"
                          className="btn btn--ghost turn__save-note"
                          onClick={() => openNoteEditor(turn)}
                        >
                          <Icon name="book" size={15} />
                          保存为笔记
                        </button>
                      )}
                      {turn.citations.length === 0 && turn.status === "insufficient" ? (
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
      {noteTurn && (
        <Modal
          title="保存为笔记"
          onClose={() => !savingNote && setNoteTurn(null)}
          busy={savingNote}
        >
          <form
            className="knowledge-form"
            onSubmit={(event) => {
              event.preventDefault();
              void saveNote();
            }}
          >
            <p className="maintenance-hint">
              保存后可在笔记中继续编辑；回答内容与当时的引用快照会一起保存。
            </p>
            <label>
              <span>笔记标题</span>
              <input
                autoFocus
                value={noteTitle}
                maxLength={200}
                onChange={(event) => setNoteTitle(event.target.value)}
                required
              />
            </label>
            <label>
              <span>笔记内容</span>
              <textarea
                value={noteBody}
                rows={12}
                maxLength={100000}
                onChange={(event) => setNoteBody(event.target.value)}
                required
              />
            </label>
            <p className="maintenance-hint">
              {noteTurn.citations.length
                ? `将保存 ${noteTurn.citations.length} 条引用快照。`
                : "这条回答没有可保存的原文引用。"}
            </p>
            <div className="maintenance-actions">
              <button
                type="button"
                className="btn btn--ghost"
                disabled={savingNote}
                onClick={() => setNoteTurn(null)}
              >
                取消
              </button>
              <button
                className="btn"
                disabled={savingNote || !noteTitle.trim() || !noteBody.trim()}
              >
                {savingNote ? "保存中…" : "保存笔记"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}
