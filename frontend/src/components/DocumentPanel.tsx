import { useRef, useState } from "react";
import { api, ApiError, type DocumentItem } from "../lib/api";
import { Icon } from "./Icon";
type Props = { teamId: string; knowledgeBaseId: string; documents: DocumentItem[]; loading: boolean; onChanged: () => Promise<void>; onError: (message: string) => void; };
const STATUS_LABEL: Record<DocumentItem["status"], string> = { pending: "等待处理", processing: "解析中", ready: "可检索", failed: "处理失败" };
function formatSize(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
export function DocumentPanel({ teamId, knowledgeBaseId, documents, loading, onChanged, onError }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  async function handleFiles(files: FileList | null) {
    if (!files?.length || uploading) return;
    setUploading(true);
    try { for (const file of Array.from(files)) await api.uploadDocument(teamId, knowledgeBaseId, file); }
    catch (err) { onError(err instanceof ApiError ? err.message : "上传失败，请重试"); }
    finally { try { await onChanged(); } catch { onError("资料列表刷新失败，请重新加载页面"); } setUploading(false); if (inputRef.current) inputRef.current.value = ""; }
  }
  async function handleDelete(document: DocumentItem) {
    if (!window.confirm(`删除“${document.filename}”？删除后将无法继续检索该资料。`)) return;
    setDeleting(document.id);
    try { await api.deleteDocument(teamId, document.id); await onChanged(); }
    catch (err) { onError(err instanceof ApiError ? err.message : "删除失败，请重试"); }
    finally { setDeleting(null); }
  }
  const visible = documents.filter((doc) => doc.filename.toLowerCase().includes(filter.toLowerCase()));
  return <section className="documents-panel" aria-labelledby="docs-heading">
    <div className="panel__head"><div className="panel__heading"><Icon name="file" size={19} /><h2 id="docs-heading">资料</h2><span className="panel__count">{documents.length}</span></div></div>
    <div className="upload"><input ref={inputRef} className="upload__input" type="file" multiple accept=".pdf,.docx,.txt,.md" aria-label="选择上传资料" disabled={uploading} onChange={(e) => void handleFiles(e.target.files)} /><button className="btn btn--upload" disabled={uploading} onClick={() => inputRef.current?.click()}><Icon name="upload" size={18} />{uploading ? "正在上传…" : "上传资料"}</button><p className="upload__hint">PDF、DOCX、TXT、Markdown<br />每个文件不超过 20 MB</p></div>
    {documents.length > 0 && <div className="document-filter"><label className="sr-only" htmlFor="document-search">筛选资料名称</label><input id="document-search" type="search" placeholder="筛选资料名称…" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>}
    {loading ? <p className="panel__empty" role="status">正在加载资料…</p> : documents.length === 0 ? <div className="documents-empty"><span className="empty-file"><Icon name="file" size={30} /></span><h3>把资料放在这里</h3><p>上传后自动解析，<br />完成后即可向资料提问。</p></div> : visible.length === 0 ? <p className="panel__empty">没有匹配的资料，试试其他名称。</p> : <ul className="doc-list">{visible.map((doc) => <li key={doc.id} className="doc-item"><span className="doc-item__icon"><Icon name="file" size={21} /></span><div className="doc-item__main"><span className="doc-item__name" title={doc.filename}>{doc.filename}</span><div className="doc-item__meta"><span className={`status status--${doc.status}`}>{STATUS_LABEL[doc.status]}</span><span>{formatSize(doc.size_bytes)}</span></div></div><button className="icon-button doc-item__remove" onClick={() => void handleDelete(doc)} disabled={deleting !== null} aria-label={`删除 ${doc.filename}`} title="删除资料"><Icon name="trash" size={16} /></button>{doc.status === "failed" && doc.failure_reason && <p className="doc-item__reason">{doc.failure_reason}</p>}</li>)}</ul>}
    <p className="documents-panel__foot">检索范围限于当前知识库</p>
  </section>;
}
