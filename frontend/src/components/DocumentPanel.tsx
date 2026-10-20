import { useEffect, useRef, useState, type CSSProperties, type DragEvent } from "react";
import {
  api,
  ApiError,
  type DocumentFolder,
  type DocumentItem,
} from "../lib/api";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { NameDialog } from "./NameDialog";
import { PopoverMenu } from "./Popover";
import { ConfirmDialog } from "./ConfirmDialog";

type Props = {
  inert?: boolean;
  teamId: string;
  knowledgeBaseId: string;
  documents: DocumentItem[];
  loading: boolean;
  canDelete: boolean;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
};

type DropTarget =
  | { kind: "root" }
  | { kind: "folder"; id: string }
  | null;

const STATUS_LABEL: Record<DocumentItem["status"], string> = {
  uploading: "上传中",
  pending: "等待处理",
  processing: "解析中",
  ready: "可检索",
  failed: "处理失败",
  deleted: "已删除",
};

const errorMessage = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

const formatSize = (bytes: number) =>
  bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${Math.round(bytes / 1024)} KB`
      : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

const folderChildren = (folders: DocumentFolder[], parentId: string | null) =>
  folders
    .filter((folder) => folder.parent_id === parentId)
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));

const folderDocuments = (documents: DocumentItem[], folderId: string | null) =>
  documents.filter((document) => document.folder_id === folderId);

const descendants = (folders: DocumentFolder[], rootId: string) => {
  const ids = new Set<string>();
  const pending = [rootId];
  while (pending.length) {
    const parentId = pending.pop()!;
    for (const folder of folders) {
      if (folder.parent_id === parentId && !ids.has(folder.id)) {
        ids.add(folder.id);
        pending.push(folder.id);
      }
    }
  }
  return ids;
};

const folderPath = (folders: DocumentFolder[], folderId: string | null) => {
  if (!folderId) return [];
  const byId = new Map(folders.map((folder) => [folder.id, folder]));
  const path: DocumentFolder[] = [];
  const seen = new Set<string>();
  let current = byId.get(folderId);
  while (current && !seen.has(current.id)) {
    path.unshift(current);
    seen.add(current.id);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return path;
};

export function DocumentPanel({
  inert = false,
  teamId,
  knowledgeBaseId,
  documents,
  loading,
  canDelete,
  onChanged,
  onError,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadFolderId = useRef<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [folders, setFolders] = useState<DocumentFolder[]>([]);
  const [folderLoading, setFolderLoading] = useState(true);
  const [folderError, setFolderError] = useState("");
  const [folderRevision, setFolderRevision] = useState(0);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [nameDialog, setNameDialog] = useState<{
    mode: "create" | "rename";
    parentId: string | null;
    folder?: DocumentFolder;
  } | null>(null);
  const [documentEdit, setDocumentEdit] = useState<DocumentItem | null>(null);
  const [preview, setPreview] = useState<DocumentItem | null>(null);
  const [confirming, setConfirming] = useState<
    { kind: "document"; document: DocumentItem } | { kind: "folder"; folder: DocumentFolder } | null
  >(null);
  const [drag, setDrag] = useState<{
    kind: "folder" | "document";
    id: string;
  } | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listFolders(teamId, knowledgeBaseId)
      .then((items) => {
        if (!cancelled) {
          setFolders(items);
          setFolderError("");
        }
      })
      .catch((err) => {
        if (!cancelled) setFolderError(errorMessage(err, "加载目录失败"));
      })
      .finally(() => {
        if (!cancelled) setFolderLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, knowledgeBaseId, folderRevision]);

  const selectedPath = folderPath(folders, selectedFolderId);
  const uploadTarget = selectedPath.at(-1) ?? null;
  const query = filter.trim().toLocaleLowerCase("zh-CN");

  function selectFolder(folderId: string | null) {
    setSelectedFolderId(folderId);
    if (folderId) {
      setExpanded((current) => {
        const next = new Set(current);
        for (const item of folderPath(folders, folderId)) next.add(item.id);
        return next;
      });
    }
  }

  function updateFolders(next: DocumentFolder[]) {
    setFolders(next.slice().sort((left, right) => left.name.localeCompare(right.name, "zh-CN")));
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length || uploading) return;
    const targetFolderId = uploadFolderId.current ?? uploadTarget?.id ?? null;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(
          teamId,
          knowledgeBaseId,
          file,
          targetFolderId,
        );
      }
    } catch (err) {
      onError(errorMessage(err, "上传失败，请重试"));
    } finally {
      try {
        await onChanged();
      } catch {
        onError("资料列表刷新失败，请重新加载页面");
      }
      setUploading(false);
      uploadFolderId.current = null;
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function chooseUploadTarget(folderId: string | null) {
    uploadFolderId.current = folderId;
    if (folderId) selectFolder(folderId);
    inputRef.current?.click();
  }

  async function deleteDocument(document: DocumentItem) {
    setBusy(document.id);
    try {
      await api.deleteDocument(teamId, document.id);
      setPreview(null);
      try {
        await onChanged();
      } catch {
        onError("资料已删除，但资料列表刷新失败，请重新加载页面");
      }
    } catch (err) {
      // 由确认弹窗展示失败原因，便于在原位重试。
      throw new Error(errorMessage(err, "删除失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  async function retryDocument(document: DocumentItem) {
    setBusy(document.id);
    try {
      await api.retryDocument(teamId, document.id);
      await onChanged();
    } catch (err) {
      onError(errorMessage(err, "重试失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  async function deleteFolder(folder: DocumentFolder) {
    setBusy(folder.id);
    try {
      await api.deleteFolder(teamId, knowledgeBaseId, folder.id);
      const removedIds = new Set([folder.id, ...descendants(folders, folder.id)]);
      updateFolders(folders.filter((item) => !removedIds.has(item.id)));
      if (selectedFolderId !== null && removedIds.has(selectedFolderId)) {
        setSelectedFolderId(null);
      }
      setPreview(null);
      try {
        await onChanged();
      } catch {
        onError("目录已删除，但资料列表刷新失败，请重新加载页面");
      }
    } catch (err) {
      throw new Error(errorMessage(err, "删除目录失败"));
    } finally {
      setBusy(null);
    }
  }

  async function move(
    kind: "folder" | "document",
    id: string,
    targetFolderId: string | null,
  ) {
    if (kind === "folder") {
      const folder = folders.find((item) => item.id === id);
      if (!folder || folder.parent_id === targetFolderId) return;
      if (targetFolderId && descendants(folders, id).has(targetFolderId)) {
        onError("不能把目录移动到自身的子目录");
        return;
      }
      setBusy(id);
      try {
        const updated = await api.moveFolder(
          teamId,
          knowledgeBaseId,
          id,
          targetFolderId,
        );
        updateFolders(
          folders.map((item) => (item.id === id ? updated : item)),
        );
        if (targetFolderId) {
          setExpanded((current) => new Set([...current, targetFolderId]));
        }
      } catch (err) {
        onError(errorMessage(err, "移动目录失败"));
      } finally {
        setBusy(null);
      }
      return;
    }

    const document = documents.find((item) => item.id === id);
    if (!document || document.folder_id === targetFolderId) return;
    setBusy(id);
    try {
      await api.updateDocument(teamId, id, { folder_id: targetFolderId });
      await onChanged();
    } catch (err) {
      onError(errorMessage(err, "移动资料失败"));
    } finally {
      setBusy(null);
    }
  }

  function folderDeleteDescription(folder: DocumentFolder) {
    const folderIds = new Set([folder.id, ...descendants(folders, folder.id)]);
    const documentCount = documents.filter(
      (document) => document.folder_id && folderIds.has(document.folder_id),
    ).length;
    const childCount = folderIds.size - 1;
    return (
      <>
        <p>删除后，这个目录下的全部内容都无法恢复：</p>
        <ul>
          <li>
            {documentCount
              ? `将删除 ${documentCount} 份资料及其原始文件`
              : "目录内没有资料"}
          </li>
          <li>
            {childCount ? `将同时删除 ${childCount} 个子目录` : "目录内没有子目录"}
          </li>
        </ul>
        <p>相关资料将无法继续检索，引用它的分析结果可能失效；团队存储额度会同步释放。</p>
      </>
    );
  }

  function dropOn(
    event: DragEvent,
    target: Exclude<DropTarget, null>,
  ) {
    event.preventDefault();
    event.stopPropagation();
    setDropTarget(null);
    if (!drag || !canDelete || busy) return;
    const targetId = target.kind === "root" ? null : target.id;
    void move(drag.kind, drag.id, targetId);
  }

  function folderRow(folder: DocumentFolder, depth: number) {
    const children = folderChildren(folders, folder.id);
    const files = folderDocuments(documents, folder.id).filter(
      (document) =>
        !query || document.filename.toLocaleLowerCase("zh-CN").includes(query),
    );
    const folderMatches =
      !query || folder.name.toLocaleLowerCase("zh-CN").includes(query);
    const isOpen = Boolean(query) || expanded.has(folder.id);
    const visibleChildren = query ? children.filter((child) => {
      const childIds = new Set([child.id, ...descendants(folders, child.id)]);
      return (
        child.name.toLocaleLowerCase("zh-CN").includes(query) ||
        documents.some(
          (document) =>
            childIds.has(document.folder_id ?? "") &&
            document.filename.toLocaleLowerCase("zh-CN").includes(query),
        )
      );
    }) : children;
    if (query && !folderMatches && !files.length && !visibleChildren.length) return null;
    const isDrop = dropTarget?.kind === "folder" && dropTarget.id === folder.id;

    return (
      <li
        key={folder.id}
        className={`tree-folder${isDrop ? " is-drop-target" : ""}`}
        style={{ "--tree-depth": depth } as CSSProperties}
        draggable={canDelete && busy === null}
        onDragStart={(event) => {
          event.stopPropagation();
          setDrag({ kind: "folder", id: folder.id });
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", folder.name);
        }}
        onDragEnd={() => {
          setDrag(null);
          setDropTarget(null);
        }}
        onDragOver={(event) => {
          if (!drag || !canDelete) return;
          event.preventDefault();
          event.stopPropagation();
          setDropTarget({ kind: "folder", id: folder.id });
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDropTarget((current) =>
              current?.kind === "folder" && current.id === folder.id ? null : current,
            );
          }
        }}
        onDrop={(event) => dropOn(event, { kind: "folder", id: folder.id })}
      >
        <div className={`tree-folder__row${selectedFolderId === folder.id ? " is-selected" : ""}`}>
          <button
            type="button"
            className="tree-folder__expand"
            aria-expanded={isOpen}
            aria-label={`${isOpen ? "收起" : "展开"}目录 ${folder.name}`}
            title={isOpen ? "收起" : "展开"}
            onClick={() => {
              setExpanded((current) => {
                const next = new Set(current);
                if (next.has(folder.id)) next.delete(folder.id);
                else next.add(folder.id);
                return next;
              });
            }}
          >
            <Icon
              name="chevron"
              size={13}
              style={{ transform: isOpen ? undefined : "rotate(-90deg)" }}
            />
          </button>
          <button
            type="button"
            className="tree-folder__toggle"
            aria-pressed={selectedFolderId === folder.id}
            onClick={() => selectFolder(folder.id)}
          >
            <Icon name="folder" size={16} />
            <span>{folder.name}</span>
            <small>{folderDocuments(documents, folder.id).length}</small>
          </button>
          {canDelete && (
            <div className="tree-folder__actions">
              <button
                type="button"
                className="tree-quick-action"
                aria-label={`在 ${folder.name} 中新建目录`}
                title="新建目录"
                onClick={() =>
                  setNameDialog({
                    mode: "create",
                    parentId: folder.id,
                  })
                }
              >
                <Icon name="plus" size={15} />
              </button>
              <PopoverMenu
                label={`管理目录 ${folder.name}`}
                className="tree-menu"
                trigger={<Icon name="more" size={15} />}
              >
                {(close) => <>
                  <button type="button" role="menuitem" onClick={() => { close(); chooseUploadTarget(folder.id); }}><Icon name="upload" size={15} />上传到这里</button>
                  <button type="button" role="menuitem" onClick={() => { close(); setNameDialog({ mode: "rename", parentId: folder.parent_id, folder }); }}><Icon name="edit" size={15} />重命名</button>
                  <button type="button" role="menuitem" className="is-danger" disabled={busy !== null} onClick={() => { close(); setConfirming({ kind: "folder", folder }); }}><Icon name="trash" size={15} />删除</button>
                </>}
              </PopoverMenu>
            </div>
          )}
        </div>
        {isOpen && (
          <ul className="tree-folders">
            {visibleChildren.map((child) => folderRow(child, depth + 1))}
            {files.map((document) => documentRow(document, depth + 1))}
            {!files.length && !visibleChildren.length && (
              <li className="tree-empty">空目录</li>
            )}
          </ul>
        )}
      </li>
    );
  }

  function documentRow(document: DocumentItem, depth: number) {
    if (query && !document.filename.toLocaleLowerCase("zh-CN").includes(query)) {
      return null;
    }
    return (
      <li key={document.id} className="tree-document" style={{ "--tree-depth": depth } as CSSProperties}>
        <Icon name="file" size={16} />
        <button
          className="tree-document__name"
          type="button"
          title={document.filename}
          draggable={canDelete && busy === null}
          onDragStart={(event) => {
            event.stopPropagation();
            setDrag({ kind: "document", id: document.id });
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", document.filename);
          }}
          onDragEnd={() => {
            setDrag(null);
            setDropTarget(null);
          }}
          onClick={() => setPreview(document)}
        >
          {document.filename}
          <small className={`status status--${document.status}`}>
            {STATUS_LABEL[document.status]}
          </small>
        </button>
        {document.status === "failed" && (
          <button
            type="button"
            className="doc-action"
            onClick={() => void retryDocument(document)}
            disabled={busy !== null}
          >
            重试
          </button>
        )}
        {canDelete && (
          <PopoverMenu
            label={`管理资料 ${document.filename}`}
            className="tree-menu"
            trigger={<Icon name="more" size={15} />}
          >
            {(close) => <>
              <button type="button" role="menuitem" onClick={() => { close(); setDocumentEdit(document); }}><Icon name="edit" size={15} />重命名</button>
              <button type="button" role="menuitem" className="is-danger" disabled={busy !== null} onClick={() => { close(); setConfirming({ kind: "document", document }); }}><Icon name="trash" size={15} />删除</button>
            </>}
          </PopoverMenu>
        )}
      </li>
    );
  }

  const visible = ({
    rootFolders: folderChildren(folders, null).filter((folder) => {
      if (!query) return true;
      const childIds = new Set([folder.id, ...descendants(folders, folder.id)]);
      return (
        folder.name.toLocaleLowerCase("zh-CN").includes(query) ||
        documents.some(
          (document) =>
            childIds.has(document.folder_id ?? "") &&
            document.filename.toLocaleLowerCase("zh-CN").includes(query),
        )
      );
    }),
    unfiled: folderDocuments(documents, null).filter(
      (document) =>
        !query || document.filename.toLocaleLowerCase("zh-CN").includes(query),
    ),
  });
  const hasVisibleResults =
    visible.rootFolders.length > 0 || visible.unfiled.length > 0;

  return (
    <section className="documents-panel" aria-labelledby="docs-heading" inert={inert}>
      <div className="panel__head">
        <div className="panel__heading">
          <Icon name="folder" size={18} />
          <h2 id="docs-heading">资料目录</h2>
          <span className="panel__count">{documents.length}</span>
        </div>
        {canDelete && (
          <button
            type="button"
            className="icon-button"
            disabled={busy !== null || folderLoading}
            onClick={() => setNameDialog({ mode: "create", parentId: null })}
            aria-label="新建目录"
            title="新建目录"
          >
            <Icon name="plus" size={17} />
          </button>
        )}
      </div>

      <div className="directory-upload">
        <input
          ref={inputRef}
          className="upload__input"
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md"
          aria-label="选择上传资料"
          disabled={uploading || folderLoading}
          onChange={(event) => void handleFiles(event.target.files)}
        />
        <button
          className="btn"
          type="button"
          disabled={uploading || folderLoading}
          onClick={() => chooseUploadTarget(selectedFolderId)}
        >
          <Icon name="upload" size={16} />
          {uploading ? "正在上传…" : "上传资料"}
        </button>
        <small className="directory-upload__target">
          {selectedPath.length ? selectedPath.map((item) => item.name).join(" / ") : "未分类"}
        </small>
      </div>

      <div className="document-filter">
        <label className="sr-only" htmlFor="document-search">搜索资料与目录</label>
        <input
          id="document-search"
          type="search"
          placeholder="搜索资料与目录"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </div>

      {folderError && (
        <p className="error-text directory-error" role="alert">
          {folderError}
          <button
            type="button"
            className="doc-action"
            onClick={() => setFolderRevision((value) => value + 1)}
          >
            重试
          </button>
        </p>
      )}

      <nav
        className={`directory-tree${dropTarget?.kind === "root" ? " is-drop-root" : ""}`}
        aria-label="资料树状目录"
        onDragOver={(event) => {
          if (!drag || !canDelete) return;
          event.preventDefault();
          setDropTarget({ kind: "root" });
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDropTarget((current) => current?.kind === "root" ? null : current);
          }
        }}
        onDrop={(event) => dropOn(event, { kind: "root" })}
      >
        <button
          type="button"
          className={`tree-root${selectedFolderId === null ? " is-selected" : ""}`}
          onClick={() => selectFolder(null)}
        >
          <Icon name="book" size={16} />
          <span>全部资料</span>
          <small>{documents.length}</small>
        </button>
        {loading || folderLoading ? (
          <p className="panel__empty" role="status">正在加载资料…</p>
        ) : (
          <ul className="tree-folders">
            {visible.rootFolders.map((folder) => folderRow(folder, 0))}
            {visible.unfiled.map((document) => documentRow(document, 0))}
            {!hasVisibleResults && (
              <li className="tree-empty tree-empty--root">
                {query ? "没有匹配的资料或目录" : "还没有资料"}
              </li>
            )}
          </ul>
        )}
      </nav>

      {nameDialog && (
        <NameDialog
          title={nameDialog.mode === "create" ? "新建目录" : "重命名目录"}
          label="目录名称"
          initial={nameDialog.folder?.name ?? ""}
          onClose={() => setNameDialog(null)}
          onSave={async (name) => {
            if (nameDialog.mode === "rename" && nameDialog.folder) {
              const updated = await api.renameFolder(
                teamId,
                knowledgeBaseId,
                nameDialog.folder.id,
                name,
              );
              updateFolders(
                folders.map((item) =>
                  item.id === updated.id ? updated : item,
                ),
              );
              return;
            }
            const created = await api.createFolder(
              teamId,
              knowledgeBaseId,
              name,
              nameDialog.parentId,
            );
            updateFolders([...folders, created]);
            setSelectedFolderId(created.id);
            setExpanded((current) => {
              const next = new Set(current);
              if (created.parent_id) next.add(created.parent_id);
              next.add(created.id);
              return next;
            });
          }}
        />
      )}

      {documentEdit && (
        <DocumentEditDialog
          document={documentEdit}
          onClose={() => setDocumentEdit(null)}
          onSave={async (filename) => {
            await api.updateDocument(teamId, documentEdit.id, { filename });
            await onChanged();
          }}
        />
      )}

      {preview && (
        <Modal title="资料详情" onClose={() => setPreview(null)}>
          <div className="document-preview">
            <Icon name="file" size={30} />
            <h3>{preview.filename}</h3>
            <p>{STATUS_LABEL[preview.status]} · {formatSize(preview.size_bytes)}</p>
            <dl>
              <dt>所在目录</dt>
              <dd>{folderPath(folders, preview.folder_id).map((item) => item.name).join(" / ") || "未分类"}</dd>
              <dt>上传时间</dt>
              <dd>{new Date(preview.created_at).toLocaleString("zh-CN")}</dd>
            </dl>
            {preview.failure_reason && <p className="error-text">{preview.failure_reason}</p>}
            <div className="maintenance-actions">
              {canDelete && (
                <button
                  className="btn btn--ghost"
                  type="button"
                  onClick={() => {
                    setPreview(null);
                    setDocumentEdit(preview);
                  }}
                >
                  重命名
                </button>
              )}
              <a
                className="btn"
                href={`/api/teams/${teamId}/documents/${preview.id}/content`}
                target="_blank"
                rel="noopener noreferrer"
              >
                打开原文件
                <Icon name="external" size={15} />
              </a>
            </div>
          </div>
        </Modal>
      )}

      {confirming && (
        <ConfirmDialog
          title={confirming.kind === "document" ? `删除资料“${confirming.document.filename}”？` : `删除目录“${confirming.folder.name}”？`}
          confirmLabel="删除"
          tone="danger"
          onClose={() => setConfirming(null)}
          onConfirm={() => confirming.kind === "document" ? deleteDocument(confirming.document) : deleteFolder(confirming.folder)}
          description={confirming.kind === "document" ? (
            <>
              <p>删除后这份资料无法继续检索，相关分析结果可能失效。</p>
              <p>原始文件会被清理，操作无法恢复。</p>
            </>
          ) : folderDeleteDescription(confirming.folder)}
        />
      )}
    </section>
  );
}

function DocumentEditDialog({
  document,
  onSave,
  onClose,
}: {
  document: DocumentItem;
  onSave: (filename: string) => Promise<void>;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [filename, setFilename] = useState(document.filename);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    const previous = window.document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);

  async function save() {
    if (saving || !filename.trim()) return;
    setSaving(true);
    setError("");
    try {
      await onSave(filename.trim());
      onClose();
    } catch (err) {
      setError(errorMessage(err, "保存失败，请重试"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="maintenance-dialog"
      aria-labelledby="document-rename-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!saving) onClose();
      }}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <h2 id="document-rename-title">重命名资料</h2>
        <label className="field">
          <span>资料名称</span>
          <input
            autoFocus
            value={filename}
            onChange={(event) => setFilename(event.target.value)}
            maxLength={300}
            disabled={saving}
            required
          />
        </label>
        <p className="maintenance-hint">保留文件扩展名。</p>
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="maintenance-actions">
          <button className="btn btn--ghost" type="button" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" disabled={saving || !filename.trim()}>{saving ? "保存中…" : "保存"}</button>
        </div>
      </form>
    </dialog>
  );
}
