export const SUPPORTED_DOCUMENT_EXTENSIONS = ["pdf", "doc", "docx", "txt", "md", "html", "htm"] as const;
export const SUPPORTED_DOCUMENT_ACCEPT = SUPPORTED_DOCUMENT_EXTENSIONS.map((value) => `.${value}`).join(",");

export function splitFilename(filename: string): { stem: string; extension: string } {
  const dot = filename.lastIndexOf(".");
  if (dot <= 0) return { stem: filename, extension: "" };
  return { stem: filename.slice(0, dot), extension: filename.slice(dot) };
}

export function fileExtension(filename: string): string {
  return splitFilename(filename).extension.slice(1).toLowerCase() || "file";
}
