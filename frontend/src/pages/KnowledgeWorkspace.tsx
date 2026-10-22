import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, type DocumentItem } from "../lib/api";
import { useCompactLayout } from "../lib/use-compact-layout";
import { readPreference, writePreference } from "../lib/workspace-preferences";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { AskPanel } from "../components/AskPanel";
import { AutomationPanel } from "../components/AutomationPanel";
import { DocumentPanel } from "../components/DocumentPanel";
import { NotesPanel } from "../components/NotesPanel";
import { ReportsPanel } from "../components/ReportsPanel";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { EvidenceProvider } from "../components/EvidenceProvider";
import { useEvidence } from "../lib/evidence-context";
import { useToast } from "../lib/toast-context";
import { Icon } from "../components/Icon";
import type { Library } from "./WorkspacePage";

export function KnowledgeWorkspace({ library }: { library: Library }) {
  return <EvidenceProvider><KnowledgeContents library={library} /></EvidenceProvider>;
}
function KnowledgeContents({ library }: { library: Library }) {
  const { session } = useAuth();
  const toast = useToast();
  const evidence = useEvidence();
  const compact = useCompactLayout();
  const { team } = library;
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const modeKey = `${session?.user.id}:${team.id}:${library.id}:mode`;
  const savedMode = readPreference(modeKey);
  type WorkspaceMode = "ask" | "analysis" | "automation" | "notes" | "reports";
  const [mode, setMode] = useState<WorkspaceMode>(
    savedMode === "analysis" ||
      savedMode === "automation" ||
      savedMode === "notes" ||
      savedMode === "reports"
      ? savedMode
      : "ask",
  );
  const [activeAnalysisId, setActiveAnalysisId] = useState<string | null>(null);
  const [activeReportId, setActiveReportId] = useState<string | null>(null);
  const [reportsDirty, setReportsDirty] = useState(false);
  const [treeVisible, setTreeVisible] = useState(false);
  const processing = documents.some((doc) => ["uploading", "pending", "processing"].includes(doc.status));
  const closeEvidence = evidence.close;
  const report = useCallback(
    (value: string) => toast.notify(value, { kind: "error" }),
    [toast],
  );
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
        if (!cancelled) setDocuments(items);
      } catch (err) {
        if (!cancelled) report(err instanceof Error ? err.message : "加载资料失败，请重试");
      }
      finally { if (!cancelled) { setLoading(false); if (shouldPoll) timer = setTimeout(() => void load(), 3000); } }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [team.id, library.id, processing, report, revision]);
  function switchMode(value: WorkspaceMode) {
    if (mode === "reports" && reportsDirty && value !== "reports" && !window.confirm("报告有未保存修改，离开将丢失。确定继续？")) return;
    closeEvidence();
    setMode(value);
    writePreference(modeKey, value);
  }
  return <div className={`knowledge-shell${evidence.data ? " has-evidence" : ""}${treeVisible ? " is-tree-visible" : ""}`}>
    <DocumentPanel inert={compact && !!evidence.data} teamId={team.id} knowledgeBaseId={library.id} documents={documents} loading={loading} canDelete={team.role === "admin"} onChanged={refresh} onError={report} />
    <main id="workspace-main" className="knowledge-main" tabIndex={-1} inert={compact && !!evidence.data}>
      <h1 className="sr-only">{library.name}</h1>
      <div className="knowledge-toolbar"><div className="knowledge-mode-switch" aria-label="工作模式"><button type="button" aria-pressed={mode === "ask"} onClick={() => switchMode("ask")}><Icon name="quote" size={16} />问答</button><button type="button" aria-pressed={mode === "analysis"} onClick={() => switchMode("analysis")}><Icon name="book" size={16} />分析</button><button type="button" aria-pressed={mode === "automation"} onClick={() => switchMode("automation")}><Icon name="send" size={16} />自动成稿</button><button type="button" aria-pressed={mode === "notes"} onClick={() => switchMode("notes")}><Icon name="edit" size={16} />笔记</button><button type="button" aria-pressed={mode === "reports"} onClick={() => switchMode("reports")}><Icon name="file" size={16} />报告</button></div><button type="button" className="tree-toggle" aria-expanded={treeVisible} onClick={() => setTreeVisible((value) => !value)}><Icon name="folder" size={16} />资料</button></div>
      {mode === "ask" ? <AskPanel teamId={team.id} knowledgeBaseId={library.id} documents={documents} canAsk={documents.some((doc) => doc.status === "ready")} processing={processing} onError={report} /> : mode === "analysis" ? <AnalysisPanel teamId={team.id} teamName={team.name} knowledgeBaseId={library.id} knowledgeBaseName={library.name} documents={documents} initialTaskId={activeAnalysisId} onError={report} onReportCreated={(reportId) => { setActiveReportId(reportId); switchMode("reports"); }} /> : mode === "automation" ? <AutomationPanel teamId={team.id} teamName={team.name} knowledgeBaseId={library.id} knowledgeBaseName={library.name} documents={documents} onError={report} onOpenAnalysis={(analysisId) => { setActiveAnalysisId(analysisId); switchMode("analysis"); }} onOpenReport={(reportId) => { setActiveReportId(reportId); switchMode("reports"); }} /> : mode === "notes" ? <NotesPanel teamId={team.id} knowledgeBaseId={library.id} documents={documents} onError={report} /> : <ReportsPanel teamId={team.id} knowledgeBaseId={library.id} documents={documents} initialReportId={activeReportId} onError={report} onPersistenceChange={setReportsDirty} />}
    </main>
    <EvidenceDrawer teamId={team.id} />
  </div>;
}
