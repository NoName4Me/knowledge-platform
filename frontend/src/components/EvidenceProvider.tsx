import { useEffect, useRef, useState, useCallback, type ReactNode } from "react";
import { Context, type EvidenceRequest, type OpenEvidence } from "../lib/evidence-context";
import { groupEvidenceTables } from "../lib/citation-text";

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<OpenEvidence | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const requestId = useRef(0);
  const returnFocus = useRef<HTMLElement | null>(null);
  const restoreFocus = useRef(false);
  const close = useCallback(() => {
    requestId.current += 1; setData(null); setSelected(null); setHovered(null);
    restoreFocus.current = true;
  }, []);
  const open = useCallback((request: EvidenceRequest) => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const revision = ++requestId.current;
    setSelected(request.selected ?? null); setHovered(null);
    // 每次打开重新读取服务端，旧摘录不作加载占位，删除/撤权后不会从本地副本恢复。
    setData({ ...request, sources: [], loading: true, error: "" });
    void request.load().then((sources) => {
      if (revision !== requestId.current) return;
      const groupedSources = groupEvidenceTables(sources);
      setData({ ...request, sources: groupedSources, loading: false, error: "" });
      const requestedSource = sources.find((source) => source.id === request.selected);
      const tableTarget = requestedSource?.table
        ? `${requestedSource.documentId}:${requestedSource.table.number}`
        : null;
      setSelected(
        tableTarget && groupedSources.some((source) => source.id === tableTarget)
          ? tableTarget
          : request.selected && groupedSources.some((source) => source.id === request.selected)
            ? request.selected
            : groupedSources[0]?.id ?? null,
      );
    }).catch((error) => {
      if (revision !== requestId.current) return;
      setData({ ...request, sources: [], loading: false, error: error instanceof Error ? error.message : "引用加载失败，请重试" });
    });
  }, []);
  useEffect(() => () => { requestId.current += 1; }, []);
  useEffect(() => {
    if (!data && restoreFocus.current) {
      restoreFocus.current = false;
      if (returnFocus.current?.isConnected) returnFocus.current.focus({ preventScroll: true });
    }
  }, [data]);
  return <Context.Provider value={{ data, activeId: hovered ?? selected, hoveredId: hovered, open, close, select: setSelected, hover: setHovered }}>{children}</Context.Provider>;
}
