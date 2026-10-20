import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, type DocumentItem } from "../lib/api";
import { useCompactLayout } from "../lib/use-compact-layout";
import { readPreference, writePreference } from "../lib/workspace-preferences";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { AskPanel } from "../components/AskPanel";
import { DocumentPanel } from "../components/DocumentPanel";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { EvidenceProvider } from "../components/EvidenceProvider";
import { useEvidence } from "../lib/evidence-context";
import { Icon } from "../components/Icon";
import type { Library } from "./WorkspacePage";

export function KnowledgeWorkspace({ library }: { library: Library }) {
  return <EvidenceProvider><KnowledgeContents library={library} /></EvidenceProvider>;
}
function KnowledgeContents({ library }: { library: Library }) {
  const { session } = useAuth();
  const evidence = useEvidence();
  const compact = useCompactLayout();
  const { team } = library;
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const modeKey = `${session?.user.id}:${team.id}:${library.id}:mode`;
  const [mode, setMode] = useState<"ask" | "analysis">(() => readPreference(modeKey) === "analysis" ? "analysis" : "ask");
  const [treeVisible, setTreeVisible] = useState(false);
  const processing = documents.some((doc) => ["uploading", "pending", "processing"].includes(doc.status));
  const closeEvidence = evidence.close;
  const refresh = useCallback(async () => {
    closeEvidence();
    setDocuments(await api.listDocuments(team.id, library.id));
    setRevision((value) => value + 1);
  }, [closeEvidence, team.id, library.id]);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      let shouldPoll = processing;
      try {
        const items = await api.listDocuments(team.id, library.id);
        shouldPoll = items.some((doc) => ["uploading", "pending", "processing"].includes(doc.status));
        if (!cancelled) { setDocuments(items); setError(""); }
      } catch (err) { if (!cancelled) setError(err instanceof Error ? err.message : "加载资料失败，请重试"); }
      finally { if (!cancelled) { setLoading(false); if (shouldPoll) timer = setTimeout(() => void load(), 3000); } }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [team.id, library.id, processing, revision]);
  function switchMode(value: "ask" | "analysis") { closeEvidence(); setMode(value); writePreference(modeKey, value); }
  return <div className={`knowledge-shell${evidence.data ? " has-evidence" : ""}${treeVisible ? " is-tree-visible" : ""}`}>
    <DocumentPanel inert={compact && !!evidence.data} teamId={team.id} knowledgeBaseId={library.id} documents={documents} loading={loading} canDelete={team.role === "admin"} onChanged={refresh} onError={setError} />
    <main id="workspace-main" className="knowledge-main" tabIndex={-1} inert={compact && !!evidence.data}>
      <h1 className="sr-only">{library.name}</h1>
      <div className="knowledge-toolbar"><div className="knowledge-mode-switch" aria-label="工作模式"><button type="button" aria-pressed={mode === "ask"} onClick={() => switchMode("ask")}><Icon name="quote" size={16} />问答</button><button type="button" aria-pressed={mode === "analysis"} onClick={() => switchMode("analysis")}><Icon name="book" size={16} />分析</button></div><button type="button" className="tree-toggle" aria-expanded={treeVisible} onClick={() => setTreeVisible((value) => !value)}><Icon name="folder" size={16} />资料</button></div>
      {error && <div className="knowledge-error" role="alert"><span>{error}</span><button className="btn btn--ghost" type="button" onClick={() => { closeEvidence(); setRevision((value) => value + 1); }}>重新加载</button></div>}
      {mode === "ask" ? <AskPanel teamId={team.id} knowledgeBaseId={library.id} canAsk={documents.some((doc) => doc.status === "ready")} processing={processing} onError={setError} /> : <AnalysisPanel teamId={team.id} teamName={team.name} knowledgeBaseId={library.id} knowledgeBaseName={library.name} documents={documents} onError={setError} />}
    </main>
    <EvidenceDrawer teamId={team.id} />
  </div>;
}
