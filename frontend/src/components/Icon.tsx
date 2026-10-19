import type { CSSProperties, ReactNode } from "react";

type IconName = "book" | "file" | "upload" | "send" | "quote" | "logout" | "chevron" | "trash" | "plus" | "shield";
const paths: Record<IconName, ReactNode> = {
  book: <><path d="M12 5v15M3 4h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z" /></>,
  file: <><path d="M14 3H5v18h14V8Z" /><path d="M14 3v5h5M8 12h8M8 16h5" /></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6" /></>,
  send: <><path d="m4 4 17 8-17 8 3-8Zm3 8h14" /></>,
  quote: <><path d="M10 6H4v7h6v-3H4m6 3c0 4-2 5-5 5M20 6h-6v7h6v-3h-6m6 3c0 4-2 5-5 5" /></>,
  logout: <><path d="M10 4H4v16h6m5-13 5 5-5 5m-7-5h12" /></>,
  chevron: <path d="m8 10 4 4 4-4" />,
  trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z" /><path d="m8 12 3 3 5-6" /></>,
};
export function Icon({ name, size = 20, style }: { name: IconName; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>{paths[name]}</svg>;
}
export function Brand() {
  return <span className="brand"><span className="brand__symbol"><Icon name="book" size={22} /></span><span>知识工作台</span></span>;
}
