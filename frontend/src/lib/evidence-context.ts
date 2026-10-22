import { createContext, useContext } from "react";

export type EvidenceTable = {
  number: number;
  headers: string[] | null;
  rows: Array<{ key: string; row: number; cells: string[]; id: string; number: number }>;
};
export type EvidenceSource = {
  id: string; number: number; documentId: string; title: string; locator: string; excerpt: string;
  table?: EvidenceTable;
};
export type EvidenceRequest = { scope: string; title: string; selected?: string; load: () => Promise<EvidenceSource[]> };
export type OpenEvidence = EvidenceRequest & { sources: EvidenceSource[]; loading: boolean; error: string };
type EvidenceState = {
  data: OpenEvidence | null; activeId: string | null; hoveredId: string | null;
  open: (request: EvidenceRequest) => void; close: () => void;
  select: (id: string) => void; hover: (id: string | null) => void;
};
export const Context = createContext<EvidenceState | null>(null);
export function useEvidence() {
  const context = useContext(Context);
  if (!context) throw new Error("引用展示必须位于知识库工作区内");
  return context;
}
