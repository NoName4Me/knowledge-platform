import { useEffect, useMemo, useState } from "react";
import {
  api,
  type DocumentItem,
  type NoteOut,
} from "../lib/api";
import { Icon } from "./Icon";
import { Modal } from "./Modal";

type Props = {
  teamId: string;
  knowledgeBaseId: string;
  documents: DocumentItem[];
  onError: (message: string) => void;
};

const formatTime = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

export function NotesPanel({
  teamId,
  knowledgeBaseId,
  documents,
  onError,
}: Props) {
  const [notes, setNotes] = useState<NoteOut[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [draftDocumentId, setDraftDocumentId] = useState("");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const selected = notes.find((note) => note.id === selectedId) ?? notes[0] ?? null;

  async function load(preselect?: string) {
    setLoading(true);
    try {
      const response = await api.listNotes(teamId, knowledgeBaseId);
      setNotes(response.items);
      setSelectedId(preselect ?? response.items[0]?.id ?? null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "加载笔记失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId, knowledgeBaseId]);

  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("zh-CN");
    if (!term) return notes;
    return notes.filter((note) =>
      `${note.title}\n${note.content}`.toLocaleLowerCase("zh-CN").includes(term),
    );
  }, [notes, query]);

  function startCreate() {
    setCreating(true);
    setDraftTitle("");
    setDraftBody("");
    setDraftDocumentId("");
  }

  async function save() {
    if (!draftTitle.trim() || !draftBody.trim() || saving) return;
    setSaving(true);
    try {
      const note = await api.createNote(teamId, knowledgeBaseId, {
        title: draftTitle.trim(),
        content: draftBody.trim(),
        source_type: draftDocumentId ? "document" : "manual",
        source_id: draftDocumentId || null,
        citations: [],
      });
      setCreating(false);
      await load(note.id);
    } catch (err) {
      onError(err instanceof Error ? err.message : "保存笔记失败");
    } finally {
      setSaving(false);
    }
  }

  async function update(note: NoteOut) {
    setBusy(true);
    try {
      const saved = await api.updateNote(teamId, knowledgeBaseId, note.id, {
        title: note.title,
        content: note.content,
        citations: note.citations,
      });
      setNotes((items) => items.map((item) => (item.id === saved.id ? saved : item)));
    } catch (err) {
      onError(err instanceof Error ? err.message : "保存笔记失败");
    } finally {
      setBusy(false);
    }
  }

  async function remove(note: NoteOut) {
    if (!window.confirm(`删除笔记“${note.title}”？此操作无法恢复。`)) return;
    setBusy(true);
    try {
      await api.deleteNote(teamId, knowledgeBaseId, note.id);
      setNotes((items) => items.filter((item) => item.id !== note.id));
      setSelectedId(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "删除笔记失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="knowledge-products" aria-labelledby="notes-heading">
      <div className="panel__head knowledge-products__head">
        <div className="panel__heading">
          <Icon name="book" size={19} />
          <h2 id="notes-heading">知识笔记</h2>
          <span className="panel__count">{notes.length}</span>
        </div>
        <button type="button" className="btn" onClick={startCreate}>
          <Icon name="plus" size={16} />新建笔记
        </button>
      </div>
      <div className="knowledge-products__layout">
        <aside className="knowledge-products__list" aria-label="笔记列表">
          <label className="knowledge-products__search">
            <span className="sr-only">搜索笔记</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标题或内容"
            />
          </label>
          {loading ? (
            <p className="analysis-history__state" role="status">正在加载笔记…</p>
          ) : filtered.length === 0 ? (
            <p className="analysis-history__state">
              {query ? "没有匹配的笔记。" : "保存重要结论和原文摘录后，可从资料、问答或报告继续复用。"}
            </p>
          ) : (
            <ul>
              {filtered.map((note) => (
                <li key={note.id}>
                  <button
                    type="button"
                    className={note.id === selected?.id ? "is-active" : ""}
                    onClick={() => setSelectedId(note.id)}
                  >
                    <strong>{note.title}</strong>
                    <span>{note.content.slice(0, 100)}</span>
                    <small>{formatTime(note.updated_at)}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>
        <main className="knowledge-products__main">
          {selected ? (
            <NoteEditor
              key={selected.id}
              note={selected}
              busy={busy}
              onSave={update}
              onDelete={remove}
            />
          ) : (
            <div className="knowledge-empty">
              <Icon name="book" size={30} />
              <h3>还没有笔记</h3>
              <p>记录阅读结论、制度理解或需要继续核对的要点。每篇笔记只对本人可见。</p>
              <button type="button" className="btn" onClick={startCreate}>新建第一篇笔记</button>
            </div>
          )}
        </main>
      </div>
      {creating && (
        <Modal title="新建笔记" onClose={() => !saving && setCreating(false)} busy={saving}>
          <form className="knowledge-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            <label>
              <span>标题</span>
              <input
                autoFocus
                value={draftTitle}
                onChange={(event) => setDraftTitle(event.target.value)}
                maxLength={200}
                required
              />
            </label>
            <label>
              <span>关联资料（可选）</span>
              <select value={draftDocumentId} onChange={(event) => setDraftDocumentId(event.target.value)}>
                <option value="">不关联资料</option>
                {documents.filter((document) => document.status === "ready").map((document) => (
                  <option key={document.id} value={document.id}>{document.title}</option>
                ))}
              </select>
            </label>
            <label>
              <span>内容</span>
              <textarea
                value={draftBody}
                onChange={(event) => setDraftBody(event.target.value)}
                rows={10}
                maxLength={100000}
                placeholder="记录自己的理解、摘录或待核实事项…"
                required
              />
            </label>
            <div className="maintenance-actions">
              <button type="button" className="btn btn--ghost" disabled={saving} onClick={() => setCreating(false)}>取消</button>
              <button className="btn" disabled={saving || !draftTitle.trim() || !draftBody.trim()}>
                {saving ? "保存中…" : "保存笔记"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}

function NoteEditor({
  note,
  busy,
  onSave,
  onDelete,
}: {
  note: NoteOut;
  busy: boolean;
  onSave: (note: NoteOut) => Promise<void>;
  onDelete: (note: NoteOut) => Promise<void>;
}) {
  const [title, setTitle] = useState(note.title);
  const [content, setContent] = useState(note.content);
  return (
    <article className="knowledge-editor" aria-labelledby="note-editor-heading">
      <div className="knowledge-editor__head">
        <div>
          <span className="knowledge-kicker">本人笔记 · {note.source_type === "manual" ? "手动整理" : "关联资料"}</span>
          <h2 id="note-editor-heading">{title || "未命名笔记"}</h2>
        </div>
        <div>
          <button type="button" className="btn btn--ghost" onClick={() => void onDelete(note)} disabled={busy}>
            <Icon name="trash" size={15} />删除
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy || !title.trim() || !content.trim()}
            onClick={() => void onSave({ ...note, title: title.trim(), content: content.trim() })}
          >
            {busy ? "保存中…" : "保存修改"}
          </button>
        </div>
      </div>
      <label className="knowledge-form__plain">
        <span>标题</span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} />
      </label>
      <label className="knowledge-form__plain">
        <span>内容</span>
        <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={16} maxLength={100000} />
      </label>
      {note.citations.length > 0 && (
        <section className="knowledge-evidence" aria-labelledby="note-evidence-heading">
          <h3 id="note-evidence-heading">保存时的原文依据</h3>
          {note.citations.map((citation, index) => (
            <blockquote key={`${citation.document_title}-${index}`}>
              <strong>{citation.document_title}</strong>
              <small>{citation.locator_label || "未记录位置"}</small>
              <p>{citation.excerpt}</p>
            </blockquote>
          ))}
        </section>
      )}
    </article>
  );
}
