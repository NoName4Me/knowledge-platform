import type { ComponentType } from "react";
import {
  IconFileCode,
  IconFileText,
  IconFileTypeDoc,
  IconFileTypeDocx,
  IconFileTypePdf,
  IconFileTypeTxt,
} from "@tabler/icons-react";
import { fileExtension } from "../lib/document-format";

const icons: Record<
  string,
  { component: ComponentType<{ size?: number; stroke?: number }>; label: string }
> = {
  pdf: { component: IconFileTypePdf, label: "PDF" },
  doc: { component: IconFileTypeDoc, label: "DOC" },
  docx: { component: IconFileTypeDocx, label: "DOCX" },
  txt: { component: IconFileTypeTxt, label: "TXT" },
  md: { component: IconFileCode, label: "Markdown" },
  html: { component: IconFileCode, label: "HTML" },
  htm: { component: IconFileCode, label: "HTML" },
};

export function FileTypeIcon({ filename, size = 17 }: { filename: string; size?: number }) {
  const extension = fileExtension(filename);
  const entry = icons[extension];
  const Component = entry?.component ?? IconFileText;
  return (
    <span
      className={`file-type-icon${entry ? ` file-type-icon--${extension}` : " file-type-icon--generic"}`}
      aria-hidden="true"
      title={entry?.label ?? "文件"}
    >
      <Component size={size} stroke={1.65} />
    </span>
  );
}
