import type { DocumentFolder, DocumentItem } from "./api";

export type TreeItem = { kind: "folder" | "document"; id: string };

export type TreeNode =
  | { kind: "folder"; id: string; order: number; folder: DocumentFolder }
  | { kind: "document"; id: string; order: number; document: DocumentItem };

export type TreeDrop =
  | { kind: "inside"; folderId: string }
  | { kind: "before" | "after"; parentId: string | null; targetKind: "folder" | "document"; targetId: string }
  | { kind: "root" };

export type TreeDrag = { kind: "folder" | "document"; id: string };

export function childrenOf(
  folders: DocumentFolder[],
  documents: DocumentItem[],
  parentId: string | null,
): TreeNode[] {
  const nodes: TreeNode[] = [
    ...folders
      .filter((folder) => folder.parent_id === parentId)
      .map((folder) => ({ kind: "folder" as const, id: folder.id, order: folder.sort_order ?? 0, folder })),
    ...documents
      .filter((document) => document.folder_id === parentId)
      .map((document) => ({ kind: "document" as const, id: document.id, order: document.sort_order ?? 0, document })),
  ];
  return nodes.sort((left, right) => left.order - right.order || left.id.localeCompare(right.id));
}

export function canDrop(drag: TreeDrag | null, drop: TreeDrop): boolean {
  if (!drag) return false;
  if (drop.kind === "root") return true;
  if (drop.kind === "inside") return drag.kind === "document" || drag.id !== drop.folderId;
  if (drag.kind !== drop.targetKind || drag.id !== drop.targetId) return true;
  return false;
}

export function siblingListAfterDrop(
  nodes: TreeNode[],
  drag: TreeDrag,
  targetId?: string,
  placement: "before" | "after" = "after",
  targetKind: "folder" | "document" = drag.kind,
): TreeItem[] {
  const rest = nodes.filter((node) => !(node.kind === drag.kind && node.id === drag.id));
  const movingItem = { kind: drag.kind, id: drag.id };
  if (!targetId) return [...rest.map(({ kind, id }) => ({ kind, id })), movingItem];
  const index = rest.findIndex((node) => node.kind === targetKind && node.id === targetId);
  if (index < 0) return [...rest.map(({ kind, id }) => ({ kind, id })), movingItem];
  rest.splice(index + (placement === "after" ? 1 : 0), 0, {
    kind: drag.kind,
    id: drag.id,
  } as TreeNode);
  return rest.map(({ kind, id }) => ({ kind, id }));
}
