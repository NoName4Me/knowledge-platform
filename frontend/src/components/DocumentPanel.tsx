import { Fragment, useEffect, useRef, useState, type ChangeEvent, type CSSProperties, type DragEvent } from "react";
import {
  IconClipboardText,
  IconFolderPlus,
  IconPencil,
  IconSearch,
  IconTrash,
  IconUpload,
  IconWorld,
} from "@tabler/icons-react";
import {
  api,
  ApiError,
  type DocumentGuideOut,
  type DocumentFolder,
  type DocumentItem,
  type DocumentSearchOut,
} from "../lib/api";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { NameDialog } from "./NameDialog";
import { PopoverMenu } from "./Popover";
import { ConfirmDialog } from "./ConfirmDialog";
import { FileTypeIcon } from "./FileTypeIcon";
import { SUPPORTED_DOCUMENT_ACCEPT, splitFilename } from "../lib/document-format";
import { canDrop, childrenOf, siblingListAfterDrop, type TreeDrag, type TreeDrop } from "../lib/document-tree";

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

type DropTarget = TreeDrop | null;

type DragTarget = {
  drag: TreeDrag;
  parentId: string | null;
  index: number;
};

type GapTarget = {
  parentId: string | null;
  targetKind: "folder" | "document";
  targetId: string;
};

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
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [nameDialog, setNameDialog] = useState<{
    mode: "create" | "rename";
    parentId: string | null;
    folder?: DocumentFolder;
  } | null>(null);
  const [documentEdit, setDocumentEdit] = useState<DocumentItem | null>(null);
  const [preview, setPreview] = useState<DocumentItem | null>(null);
  const [detailTab, setDetailTab] = useState<"guide" | "search">("guide");
  const [guide, setGuide] = useState<DocumentGuideOut | null>(null);
  const [guideLoading, setGuideLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResult, setSearchResult] = useState<DocumentSearchOut | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const replaceInput = useRef<HTMLInputElement>(null);
  const [replaceBusy, setReplaceBusy] = useState(false);
  const [replaceImpact, setReplaceImpact] = useState<{
    noteCount: number;
    analysisCount: number;
    reportCount: number;
    title: string;
  } | null>(null);
  const [creatingText, setCreatingText] = useState(false);
  const [textFolderId, setTextFolderId] = useState<string | null>(null);
  const [textTitle, setTextTitle] = useState("");
  const [textBody, setTextBody] = useState("");
  const [creatingWeb, setCreatingWeb] = useState(false);
  const [webFolderId, setWebFolderId] = useState<string | null>(null);
  const [webUrl, setWebUrl] = useState("");
  const [webTitle, setWebTitle] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [confirming, setConfirming] = useState<
    { kind: "document"; document: DocumentItem } | { kind: "folder"; folder: DocumentFolder } | null
  >(null);
  const [drag, setDrag] = useState<DragTarget | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget>(null);
  const [gapTarget, setGapTarget] = useState<GapTarget | null>(null);

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

  const query = filter.trim().toLocaleLowerCase("zh-CN");

  function expandFolder(folderId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      for (const item of folderPath(folders, folderId)) next.add(item.id);
      return next;
    });
  }

  function toggleFolder(folderId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
  }

  function updateFolders(next: DocumentFolder[]) {
    setFolders(next.slice().sort((left, right) =>
      (left.sort_order ?? 0) - (right.sort_order ?? 0) || left.id.localeCompare(right.id),
    ));
  }

  async function reloadFolders() {
    setFolderLoading(true);
    try {
      updateFolders(await api.listFolders(teamId, knowledgeBaseId));
      setFolderError("");
      return true;
    } catch (err) {
      setFolderError(errorMessage(err, "加载目录失败"));
      return false;
    } finally {
      setFolderLoading(false);
    }
  }

  async function handleFiles(files: FileList | null) {
    if (!files?.length || uploading) return;
    const targetFolderId = uploadFolderId.current ?? null;
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

  async function openPreview(document: DocumentItem) {
    setPreview(document);
    setDetailTab("guide");
    setGuide(null);
    setSearchQuery("");
    setSearchResult(null);
    if (document.status !== "ready") return;
    setGuideLoading(true);
    try {
      setGuide(await api.getDocumentGuide(teamId, knowledgeBaseId, document.id));
    } catch (err) {
      onError(errorMessage(err, "加载资料导读失败"));
    } finally {
      setGuideLoading(false);
    }
  }

  async function searchPreview() {
    if (!preview || !searchQuery.trim() || searchLoading) return;
    setSearchLoading(true);
    try {
      setSearchResult(
        await api.searchDocuments(
          teamId,
          knowledgeBaseId,
          searchQuery.trim(),
          preview.id,
        ),
      );
    } catch (err) {
      onError(errorMessage(err, "搜索资料正文失败"));
    } finally {
      setSearchLoading(false);
    }
  }

  async function replacePreview(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!preview || !file || replaceBusy) return;
    setReplaceBusy(true);
    try {
      const result = await api.replaceDocument(
        teamId,
        preview.id,
        knowledgeBaseId,
        file,
      );
      setReplaceImpact({
        title: result.document.title,
        noteCount: result.affected.note_count,
        analysisCount: result.affected.analysis_count,
        reportCount: result.affected.report_count,
      });
      setPreview({
        ...result.document,
        folder_id: result.document.folder_id ?? null,
        status: result.document.status as DocumentItem["status"],
      });
      setGuide(null);
      setSearchResult(null);
      await onChanged();
    } catch (err) {
      onError(errorMessage(err, "替换资料失败"));
    } finally {
      setReplaceBusy(false);
      if (replaceInput.current) replaceInput.current.value = "";
    }
  }

  async function createText() {
    if (!textTitle.trim() || !textBody.trim() || ingesting) return;
    setIngesting(true);
    try {
      await api.createTextDocument(teamId, {
        knowledge_base_id: knowledgeBaseId,
        title: textTitle.trim(),
        content: textBody.trim(),
        folder_id: textFolderId,
      });
      setCreatingText(false);
      setTextTitle("");
      setTextBody("");
      await onChanged();
    } catch (err) {
      onError(errorMessage(err, "保存粘贴文字失败"));
    } finally {
      setIngesting(false);
    }
  }

  async function createWeb() {
    if (!webUrl.trim() || ingesting) return;
    setIngesting(true);
    try {
      await api.createWebDocument(teamId, {
        knowledge_base_id: knowledgeBaseId,
        url: webUrl.trim(),
        title: webTitle.trim() || null,
        folder_id: webFolderId,
      });
      setCreatingWeb(false);
      setWebUrl("");
      setWebTitle("");
      await onChanged();
    } catch (err) {
      onError(errorMessage(err, "导入网页失败"));
    } finally {
      setIngesting(false);
    }
  }

  async function deleteFolder(folder: DocumentFolder) {
    setBusy(folder.id);
    try {
      await api.deleteFolder(teamId, knowledgeBaseId, folder.id);
      const removedIds = new Set([folder.id, ...descendants(folders, folder.id)]);
      updateFolders(folders.filter((item) => !removedIds.has(item.id)));
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
    drag: DragTarget,
    target: TreeDrop,
  ) {
    if (!canDelete || busy) return;
    const source = drag.drag;
    const siblings = childrenOf(folders, documents, drag.parentId);
    let parentId = drag.parentId;
    let items = siblings.map(({ kind, id }) => ({ kind, id }));
    if (target.kind === "inside") {
      parentId = target.folderId;
      items = siblingListAfterDrop(childrenOf(folders, documents, parentId), source);
    } else if (target.kind === "root") {
      parentId = null;
      items = siblingListAfterDrop(childrenOf(folders, documents, null), source);
    } else {
      parentId = target.parentId;
      const targetSiblings = childrenOf(folders, documents, parentId);
      items = siblingListAfterDrop(
        targetSiblings,
        source,
        target.targetId,
        target.kind,
        target.targetKind,
      );
    }
    if (source.kind === "folder" && parentId === source.id) {
      onError("不能把目录移动到自身");
      return;
    }
    if (source.kind === "folder" && parentId && descendants(folders, source.id).has(parentId)) {
      onError("不能把目录移动到自身的子目录");
      return;
    }
    setBusy(source.id);
    try {
      const updatedFolders = await api.reorderTree(
        teamId,
        knowledgeBaseId,
        parentId,
        items,
        source,
      );
      updateFolders(updatedFolders);
      if (parentId) {
        setExpanded((current) => {
          const next = new Set(current);
          for (const item of folderPath(updatedFolders, parentId)) next.add(item.id);
          return next;
        });
      }
    } catch (err) {
      onError(errorMessage(err, "移动或排序失败，请刷新后重试"));
      // 失败后以服务端树为准，避免一次失败的请求让本地目录状态残缺。
      await reloadFolders();
      try {
        await onChanged();
      } catch {
        onError("目录已重新加载，但资料列表刷新失败，请重新加载页面");
      }
      return;
    } finally {
      setBusy(null);
      setDrag(null);
      setDropTarget(null);
    }
    try {
      await onChanged();
    } catch {
      onError("移动或排序已保存，但资料列表刷新失败，请重新加载页面");
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

  function startDrag(
    event: DragEvent,
    kind: "folder" | "document",
    id: string,
    parentId: string | null,
    index: number,
  ) {
    event.stopPropagation();
    setDrag({ drag: { kind, id }, parentId, index });
    setGapTarget(null);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", id);
  }

  function dragOverItem(
    event: DragEvent<HTMLElement>,
    target: {
      kind: "folder" | "document" | "gap";
      parentId: string | null;
      targetKind: "folder" | "document";
      targetId: string;
    },
  ) {
    if (!drag || !canDelete || busy) return;
    event.preventDefault();
    event.stopPropagation();
    setGapTarget(null);
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientY - rect.top) / rect.height;
    if (target.kind === "folder" && ratio > 0.32 && ratio < 0.68) {
      if (drag.drag.kind !== "folder" || drag.drag.id !== target.targetId) {
        setDropTarget({ kind: "inside", folderId: target.targetId });
      }
      return;
    }
    const kind = target.kind === "gap" || ratio >= 0.5 ? "after" : "before";
    setDropTarget({
      kind,
      parentId: target.parentId,
      targetKind: target.targetKind,
      targetId: target.targetId,
    });
  }

  function dropOn(event: DragEvent, target: TreeDrop) {
    event.preventDefault();
    event.stopPropagation();
    setDropTarget(null);
    setGapTarget(null);
    if (!drag || !canDrop(drag.drag, target)) return;
    void move(drag, target);
  }


  function folderHasMatch(folder: DocumentFolder): boolean {
    if (!query) return true;
    if (folder.name.toLocaleLowerCase("zh-CN").includes(query)) return true;
    const childIds = new Set([folder.id, ...descendants(folders, folder.id)]);
    return documents.some(
      (document) =>
        childIds.has(document.folder_id ?? "") &&
        document.filename.toLocaleLowerCase("zh-CN").includes(query),
    );
  }

  function folderRow(folder: DocumentFolder, depth: number, parentId: string | null, index: number) {
    const isOpen = Boolean(query) || expanded.has(folder.id);
    const isInside =
      dropTarget?.kind === "inside" && dropTarget.folderId === folder.id;
    const gapHovered =
      gapTarget?.parentId === parentId &&
      gapTarget.targetKind === "folder" &&
      gapTarget.targetId === folder.id;
    const edge =
      !gapHovered && dropTarget && dropTarget.kind !== "root" && dropTarget.kind !== "inside" &&
      dropTarget.targetKind === "folder" && dropTarget.targetId === folder.id
        ? dropTarget.kind
        : null;

    return (
      <li
        key={folder.id}
        className={`tree-folder${isInside ? " is-drop-inside" : ""}${edge ? ` is-drop-${edge}` : ""}`}
        style={{ "--tree-depth": depth } as CSSProperties}
      >
        <div
          className="tree-folder__row"
          draggable={canDelete && busy === null}
          onDragStart={(event) => startDrag(event, "folder", folder.id, parentId, index)}
          onDragEnd={() => { setDrag(null); setDropTarget(null); setGapTarget(null); }}
          onDragOver={(event) => dragOverItem(event, {
            kind: "folder", parentId, targetKind: "folder", targetId: folder.id,
          })}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setDropTarget((current) => current && current.kind !== "root" ? null : current);
            }
          }}
          onDrop={(event) => dropOn(event, isInside
            ? { kind: "inside", folderId: folder.id }
            : { kind: edge ?? "after", parentId, targetKind: "folder", targetId: folder.id })}
        >
          <button
            type="button"
            className="tree-folder__expand"
            aria-expanded={isOpen}
            aria-label={`${isOpen ? "收起" : "展开"}目录 ${folder.name}`}
            title={isOpen ? "收起" : "展开"}
            onClick={() => toggleFolder(folder.id)}
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
            aria-expanded={isOpen}
            title={folder.name}
            onClick={() => toggleFolder(folder.id)}
          >
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
                onClick={() => setNameDialog({ mode: "create", parentId: folder.id })}
              >
                <Icon name="plus" size={15} />
              </button>
              <PopoverMenu
                label={`管理目录 ${folder.name}`}
                className="tree-menu"
                trigger={<Icon name="more" size={15} />}
              >
                {(close) => <>
                  <button type="button" role="menuitem" onClick={() => { close(); chooseUploadTarget(folder.id); }}><IconUpload size={15} stroke={1.7} aria-hidden="true" />上传资料</button>
                  <button type="button" role="menuitem" onClick={() => { close(); expandFolder(folder.id); setTextFolderId(folder.id); setCreatingText(true); }}><IconClipboardText size={15} stroke={1.7} aria-hidden="true" />粘贴文字</button>
                  <button type="button" role="menuitem" onClick={() => { close(); expandFolder(folder.id); setWebFolderId(folder.id); setCreatingWeb(true); }}><IconWorld size={15} stroke={1.7} aria-hidden="true" />导入网页</button>
                  <button type="button" role="menuitem" onClick={() => { close(); setNameDialog({ mode: "rename", parentId: folder.parent_id, folder }); }}><IconPencil size={15} stroke={1.7} aria-hidden="true" />重命名</button>
                  <button type="button" role="menuitem" className="is-danger" disabled={busy !== null} onClick={() => { close(); setConfirming({ kind: "folder", folder }); }}><IconTrash size={15} stroke={1.7} aria-hidden="true" />删除</button>
                </>}
              </PopoverMenu>
            </div>
          )}
        </div>
        {(Boolean(query) || expanded.has(folder.id)) && (
          <ul className="tree-children">
            {renderChildren(folder.id, depth + 1)}
          </ul>
        )}
      </li>
    );
  }

  function documentRow(document: DocumentItem, depth: number, parentId: string | null, index: number) {
    if (query && !document.filename.toLocaleLowerCase("zh-CN").includes(query)) return null;
    const gapHovered =
      gapTarget?.parentId === parentId &&
      gapTarget.targetKind === "document" &&
      gapTarget.targetId === document.id;
    const edge =
      !gapHovered && dropTarget && dropTarget.kind !== "root" && dropTarget.kind !== "inside" &&
      dropTarget.targetKind === "document" && dropTarget.targetId === document.id
        ? dropTarget.kind
        : null;
    return (
      <li
        key={document.id}
        className={`tree-document${edge ? ` is-drop-${edge}` : ""}`}
        style={{ "--tree-depth": depth } as CSSProperties}
        draggable={canDelete && busy === null}
        onDragStart={(event) => startDrag(event, "document", document.id, parentId, index)}
        onDragEnd={() => { setDrag(null); setDropTarget(null); setGapTarget(null); }}
        onDragOver={(event) => dragOverItem(event, {
          kind: "document", parentId, targetKind: "document", targetId: document.id,
        })}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDropTarget((current) => current && current.kind !== "root" ? null : current);
          }
        }}
        onDrop={(event) => dropOn(event, {
          kind: edge ?? "after", parentId, targetKind: "document", targetId: document.id,
        })}
      >
        <FileTypeIcon filename={document.filename} size={16} />
        <button
          className="tree-document__name"
          type="button"
          title={document.filename}
          onClick={() => void openPreview(document)}
        >
          <span className="tree-document__label">{document.filename}</span>
          <small className={`status status--${document.status}`}>
            {STATUS_LABEL[document.status]}
          </small>
        </button>
        {document.status === "failed" && (
          <button type="button" className="doc-action" onClick={() => void retryDocument(document)} disabled={busy !== null}>
            重试
          </button>
        )}
        {canDelete && (
          <PopoverMenu label={`管理资料 ${document.filename}`} className="tree-menu" trigger={<Icon name="more" size={15} />}>
            {(close) => <>
              <button type="button" role="menuitem" onClick={() => { close(); setDocumentEdit(document); }}><IconPencil size={15} stroke={1.7} aria-hidden="true" />重命名</button>
              <button type="button" role="menuitem" className="is-danger" disabled={busy !== null} onClick={() => { close(); setConfirming({ kind: "document", document }); }}><IconTrash size={15} stroke={1.7} aria-hidden="true" />删除</button>
            </>}
          </PopoverMenu>
        )}
      </li>
    );
  }

  function renderChildren(parentId: string | null, depth: number) {
    const nodes = childrenOf(folders, documents, parentId).filter((node) =>
      node.kind === "folder"
        ? folderHasMatch(node.folder)
        : !query || node.document.filename.toLocaleLowerCase("zh-CN").includes(query),
    );
    const rows = nodes.map((node, index) => {
      const previous = nodes[index - 1];
      const gapActive =
        Boolean(previous) &&
        gapTarget?.parentId === parentId &&
        gapTarget.targetKind === previous!.kind &&
        gapTarget.targetId === previous!.id;
      return (
        <Fragment key={`${node.kind}:${node.id}`}>
          {previous && (
            <li
              className={`tree-drop-gap${gapActive ? " is-drop-after" : ""}`}
              style={{ "--tree-depth": depth } as CSSProperties}
              aria-hidden="true"
              onDragEnter={(event) => event.preventDefault()}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                setGapTarget({
                  parentId,
                  targetKind: previous.kind,
                  targetId: previous.id,
                });
                setDropTarget({
                  kind: "after",
                  parentId,
                  targetKind: previous.kind,
                  targetId: previous.id,
                });
              }}
              onDrop={(event) => dropOn(event, { kind: "after", parentId, targetKind: previous.kind, targetId: previous.id })}
            />
          )}
          {node.kind === "folder"
            ? folderRow(node.folder, depth, parentId, index)
            : documentRow(node.document, depth, parentId, index)}
        </Fragment>
      );
    });
    const last = nodes.at(-1);
    const trailingActive =
      Boolean(last) &&
      gapTarget?.parentId === parentId &&
      gapTarget.targetKind === last!.kind &&
      gapTarget.targetId === last!.id;
    if (last) {
      rows.push(
        <li
          key={`${last.kind}:${last.id}:end-gap`}
          className={`tree-drop-gap tree-drop-gap--end${trailingActive ? " is-drop-after" : ""}`}
          style={{ "--tree-depth": depth } as CSSProperties}
          aria-hidden="true"
          onDragEnter={(event) => event.preventDefault()}
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            setGapTarget({
              parentId,
              targetKind: last.kind,
              targetId: last.id,
            });
            setDropTarget({
              kind: "after",
              parentId,
              targetKind: last.kind,
              targetId: last.id,
            });
          }}
          onDrop={(event) => dropOn(event, { kind: "after", parentId, targetKind: last.kind, targetId: last.id })}
        />,
      );
    }
    return rows;
  }

  const hasVisibleResults = !query
    ? folders.length > 0 || documents.length > 0
    : folders.some(folderHasMatch) ||
      documents.some((document) => document.filename.toLocaleLowerCase("zh-CN").includes(query));
  return (
    <section className="documents-panel" aria-label="资料目录" inert={inert}>
      <input
        ref={inputRef}
        className="upload__input"
        type="file"
        multiple
        accept={SUPPORTED_DOCUMENT_ACCEPT}
        aria-label="选择上传资料"
        disabled={uploading || folderLoading}
        onChange={(event) => void handleFiles(event.target.files)}
      />

      <div className="document-filter">
        <label className="sr-only" htmlFor="document-search">搜索资料与目录</label>
        <IconSearch size={15} stroke={1.7} aria-hidden="true" />
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

      <nav className="directory-tree" aria-label="资料树状目录">
        <div className="tree-root-row">
          <button
            type="button"
            className="tree-root"
            onClick={() => setExpanded(new Set())}
          >
            <span className="tree-root__name">全部资料</span>
            <small>{documents.length}</small>
          </button>
          <div className="tree-root__actions">
            {canDelete && (
              <button
                type="button"
                className="tree-quick-action"
                aria-label="新建根目录"
                title="新建目录"
                onClick={() => setNameDialog({ mode: "create", parentId: null })}
              >
                <Icon name="plus" size={15} />
              </button>
            )}
            <PopoverMenu
              label="管理全部资料"
              className="tree-menu tree-root-menu"
              trigger={<Icon name="more" size={15} />}
            >
              {(close) => <>
                <button type="button" role="menuitem" onClick={() => { close(); chooseUploadTarget(null); }}><IconUpload size={15} stroke={1.7} aria-hidden="true" />上传资料</button>
                <button type="button" role="menuitem" onClick={() => { close(); setTextFolderId(null); setCreatingText(true); }}><IconClipboardText size={15} stroke={1.7} aria-hidden="true" />粘贴文字</button>
                <button type="button" role="menuitem" onClick={() => { close(); setWebFolderId(null); setCreatingWeb(true); }}><IconWorld size={15} stroke={1.7} aria-hidden="true" />导入网页</button>
                {canDelete && <button type="button" role="menuitem" onClick={() => { close(); setNameDialog({ mode: "create", parentId: null }); }}><IconFolderPlus size={15} stroke={1.7} aria-hidden="true" />新建目录</button>}
              </>}
            </PopoverMenu>
          </div>
        </div>
        {loading || folderLoading ? (
          <p className="panel__empty" role="status">正在加载资料…</p>
        ) : (
          <ul className="tree-folders">
            {renderChildren(null, 0)}
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

      {creatingText && (
        <Modal title="粘贴文字" onClose={() => !ingesting && setCreatingText(false)} busy={ingesting}>
          <form className="knowledge-form" onSubmit={(event) => { event.preventDefault(); void createText(); }}>
            <p className="maintenance-hint">保存到目录：{folderPath(folders, textFolderId).map((item) => item.name).join(" / ") || "未分类"}。内容会按普通文本解析并进入现有检索范围。</p>
            <label><span>资料名称</span><input autoFocus value={textTitle} onChange={(event) => setTextTitle(event.target.value)} maxLength={300} required /></label>
            <label><span>正文</span><textarea value={textBody} onChange={(event) => setTextBody(event.target.value)} rows={12} maxLength={2_000_000} required /></label>
            <div className="maintenance-actions"><button type="button" className="btn btn--ghost" disabled={ingesting} onClick={() => setCreatingText(false)}>取消</button><button className="btn" disabled={ingesting || !textTitle.trim() || !textBody.trim()}>{ingesting ? "保存中…" : "保存并解析"}</button></div>
          </form>
        </Modal>
      )}

      {creatingWeb && (
        <Modal title="导入网页" onClose={() => !ingesting && setCreatingWeb(false)} busy={ingesting}>
          <form className="knowledge-form" onSubmit={(event) => { event.preventDefault(); void createWeb(); }}>
            <p className="maintenance-hint">只导入这个公开网页的正文，不递归抓取站点，也不执行网页脚本。保存到目录：{folderPath(folders, webFolderId).map((item) => item.name).join(" / ") || "未分类"}。</p>
            <label><span>网页地址</span><input autoFocus type="url" value={webUrl} onChange={(event) => setWebUrl(event.target.value)} placeholder="https://example.com/article" maxLength={2000} required /></label>
            <label><span>资料名称（可选）</span><input value={webTitle} onChange={(event) => setWebTitle(event.target.value)} maxLength={300} placeholder="留空时读取网页标题" /></label>
            <div className="maintenance-actions"><button type="button" className="btn btn--ghost" disabled={ingesting} onClick={() => setCreatingWeb(false)}>取消</button><button className="btn" disabled={ingesting || !webUrl.trim()}>{ingesting ? "导入中…" : "导入并解析"}</button></div>
          </form>
        </Modal>
      )}

      {preview && (
        <Modal title="资料详情" onClose={() => { setPreview(null); setReplaceImpact(null); }}>
          <div className="document-preview">
            <FileTypeIcon filename={preview.filename} size={30} />
            <h3>{preview.title}</h3>
            <p>{STATUS_LABEL[preview.status]} · {formatSize(preview.size_bytes)}</p>
            <dl>
              <dt>文件名</dt>
              <dd>{preview.filename}</dd>
              <dt>所在目录</dt>
              <dd>{folderPath(folders, preview.folder_id).map((item) => item.name).join(" / ") || "未分类"}</dd>
              <dt>上传时间</dt>
              <dd>{new Date(preview.created_at).toLocaleString("zh-CN")}</dd>
              {preview.source_url && (
                <>
                  <dt>网页来源</dt>
                  <dd><a href={preview.source_url} target="_blank" rel="noopener noreferrer">{preview.source_url}</a></dd>
                </>
              )}
            </dl>
            {preview.failure_reason && <p className="error-text">{preview.failure_reason}</p>}
            {preview.status === "ready" && (
              <section className="document-reading" aria-labelledby="document-reading-heading">
                <div className="document-reading__tabs" role="tablist" aria-label="资料阅读工具">
                  <button type="button" role="tab" aria-selected={detailTab === "guide"} onClick={() => setDetailTab("guide")}>文档导读</button>
                  <button type="button" role="tab" aria-selected={detailTab === "search"} onClick={() => setDetailTab("search")}>正文搜索</button>
                </div>
                {detailTab === "guide" ? (
                  <div className="document-guide" role="tabpanel" aria-labelledby="document-reading-heading">
                    {guideLoading ? <p role="status">正在生成导读…</p> : guide ? (
                      <>
                        <h4 id="document-reading-heading">内容导读</h4>
                        <p>{guide.summary}</p>
                        {guide.key_concepts.length > 0 && <div className="document-guide__tags">{guide.key_concepts.map((term) => <span key={term}>{term}</span>)}</div>}
                        {guide.section_labels.length > 0 && <p className="document-guide__meta">已解析位置：{guide.section_labels.slice(0, 8).join("、")}{guide.section_labels.length > 8 ? "…" : ""}</p>}
                        <div className="document-guide__questions"><strong>建议提问</strong>{guide.suggested_questions.map((question) => <span key={question}>{question}</span>)}</div>
                        <small>{guide.coverage_note}</small>
                      </>
                    ) : <p>暂无导读。</p>}
                  </div>
                ) : (
                  <div className="document-search" role="tabpanel">
                    <form className="document-search__form" onSubmit={(event) => { event.preventDefault(); void searchPreview(); }}>
                      <label className="sr-only" htmlFor="document-text-search">搜索当前资料正文</label>
                      <input id="document-text-search" type="search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索正文中的词或编号" />
                      <button className="btn" disabled={!searchQuery.trim() || searchLoading}>{searchLoading ? "搜索中…" : "搜索"}</button>
                    </form>
                    {searchResult && (
                      <div className="document-search__results" aria-live="polite">
                        <p>{searchResult.hits.length ? `找到 ${searchResult.hits.length} 处命中` : "没有找到匹配正文。"}</p>
                        {searchResult.hits.map((hit, index) => (
                          <article key={`${hit.locator_label}-${index}`}>
                            <strong>{hit.locator_label}</strong>
                            <p><span>{hit.before}</span><mark>{searchQuery.trim()}</mark><span>{hit.after}</span></p>
                          </article>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </section>
            )}
            {replaceImpact && (
              <p className="document-impact" role="status">
                新版本“{replaceImpact.title}”已加入。相关笔记 {replaceImpact.noteCount} 篇、分析 {replaceImpact.analysisCount} 项、报告 {replaceImpact.reportCount} 份可能受影响；旧成果不会自动覆盖，请按需复核或重新生成。
              </p>
            )}
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
            {canDelete && (
              <div className="document-replace">
                <input
                  ref={replaceInput}
                  className="upload__input"
                  type="file"
                  accept={SUPPORTED_DOCUMENT_ACCEPT}
                  aria-label="选择替换资料的新文件"
                  disabled={replaceBusy}
                  onChange={(event) => void replacePreview(event)}
                />
                <button type="button" className="btn btn--ghost" disabled={replaceBusy} onClick={() => replaceInput.current?.click()}>
                  {replaceBusy ? "替换中…" : "用新文件创建版本"}
                </button>
                <small>创建新解析版本；笔记、分析和报告保留原内容并提示复核。</small>
              </div>
            )}
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
  const parts = splitFilename(document.filename);
  const [stem, setStem] = useState(parts.stem);
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
    if (saving || !stem.trim()) return;
    setSaving(true);
    setError("");
    try {
      await onSave(`${stem.trim()}${parts.extension}`);
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
          <span className="filename-input">
            <input
              autoFocus
              value={stem}
              onChange={(event) => setStem(event.target.value)}
              maxLength={300 - parts.extension.length}
              disabled={saving}
              required
            />
            <span className="filename-input__suffix">{parts.extension || "无扩展名"}</span>
          </span>
        </label>
        <p className="maintenance-hint">文件类型固定，只修改扩展名前的名称。</p>
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="maintenance-actions">
          <button className="btn btn--ghost" type="button" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" disabled={saving || !stem.trim()}>{saving ? "保存中…" : "保存"}</button>
        </div>
      </form>
    </dialog>
  );
}
