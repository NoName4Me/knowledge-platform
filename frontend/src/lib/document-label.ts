import type { DocumentItem } from "./api";

type DocumentLabelFields = Pick<DocumentItem, "title" | "filename" | "replaces_id">;

export function documentLabel(document: DocumentLabelFields): string {
  if (document.filename === document.title) return document.title;
  const version = document.replaces_id ? "，新版本" : "";
  return `${document.title}（${document.filename}${version}）`;
}
