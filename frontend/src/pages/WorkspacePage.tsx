import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, ApiError, type DocumentItem, type KnowledgeBase, type TeamSummary } from "../lib/api";
import { AskPanel } from "../components/AskPanel";
import { DocumentPanel } from "../components/DocumentPanel";
import { MemberDialog } from "../components/MemberDialog";
import { TeamSidebar } from "../components/TeamSidebar";
import { Icon } from "../components/Icon";
const message = (err: unknown, fallback: string) => err instanceof ApiError ? err.message : fallback;

export function WorkspacePage() {
  const { session, logout } = useAuth();
  const [teamId, setTeamId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!session) return null;
  const team = session.teams.find((item) => item.id === teamId) ?? session.teams[0];
  return <div className="workspace">
    <a className="skip-link" href="#workspace-main">跳到工作区</a>
    <TeamSidebar teams={session.teams} activeTeamId={team?.id ?? null} onSelect={setTeamId} userName={session.user.display_name} onLogout={() => { void logout().catch((err) => setError(message(err, "退出失败，请重试"))); }} />
    <main id="workspace-main" className="workspace__main" tabIndex={-1}>
      {error && <p className="error-text" role="alert">{error}</p>}
      {team ? <TeamWorkspace key={team.id} team={team} /> : <div className="workspace__empty"><Icon name="book" size={40} /><h1>还没有加入团队</h1><p>请联系团队管理员获取邀请，加入后即可访问资料。</p></div>}
    </main>
  </div>;
}
function TeamWorkspace({ team }: { team: TeamSummary }) {
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [activeId, setActiveId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  const [membersOpen, setMembersOpen] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.listKnowledgeBases(team.id).then((items) => { if (!cancelled) { setBases(items); setLoading(false); } }).catch((err) => { if (!cancelled) { setError(message(err, "加载知识库失败，请重试")); setLoading(false); } });
    return () => { cancelled = true; };
  }, [team.id, reload]);
  const active = bases.find((kb) => kb.id === activeId) ?? bases[0];
  async function create(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    try { const kb = await api.createKnowledgeBase(team.id, name.trim()); setBases((prev) => [...prev, kb]); setActiveId(kb.id); setName(""); setCreating(false); setError(null); }
    catch (err) { setError(message(err, "创建知识库失败，请重试")); }
    finally { setSaving(false); }
  }
  return <>
    <header className="workspace__bar"><span>{team.name}<span className="breadcrumb-divider">/</span>知识库</span><span className="access-label"><Icon name="shield" size={15} /> 团队共享</span></header>
    <div className="workspace__content">
      <div className="workspace__heading"><div><h1>文档与问答</h1><p>整理团队资料，从原文中寻找答案。</p></div><div className="workspace__tools">
        {bases.length > 0 && <div className="kb-picker"><label htmlFor="knowledge-base">当前知识库</label><div><Icon name="book" size={17} /><select id="knowledge-base" value={active?.id ?? ""} onChange={(e) => setActiveId(e.target.value)}>{bases.map((kb) => <option key={kb.id} value={kb.id}>{kb.name}</option>)}</select></div></div>}
        {team.role === "admin" && <button className="btn btn--ghost" onClick={() => setMembersOpen(true)}><Icon name="shield" size={17} />成员</button>}
        {team.role === "admin" && <button className="btn btn--ghost" onClick={() => setCreating(!creating)} aria-expanded={creating}><Icon name="plus" size={17} />新建知识库</button>}
      </div></div>
      {membersOpen && <MemberDialog teamId={team.id} onClose={() => setMembersOpen(false)} onError={setError} />}
      {creating && <form className="create-form" onSubmit={create}><div className="field"><label htmlFor="kb-name">知识库名称</label><input id="kb-name" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：产品资料" required /></div><button className="btn" disabled={saving || !name.trim()}>{saving ? "创建中…" : "创建知识库"}</button><button type="button" className="btn btn--ghost" onClick={() => setCreating(false)}>取消</button></form>}
      {error && <div className="error-banner" role="alert">{error}<button className="btn btn--ghost" onClick={() => { setError(null); setLoading(true); setReload((n) => n + 1); }}>重新加载</button></div>}
      {loading ? <p className="workspace__note" role="status">正在加载知识库…</p> : active ? <KnowledgeWorkspace key={active.id} team={team} knowledgeBase={active} /> : !error && <div className="workspace__empty"><Icon name="book" size={40} /><h2>从一个知识库开始</h2><p>{team.role === "admin" ? "创建知识库，按主题整理资料，再向资料提问。" : "管理员创建知识库后，你就可以上传资料并提问。"}</p>{team.role === "admin" && <button className="btn" onClick={() => setCreating(true)}><Icon name="plus" size={18} />新建知识库</button>}</div>}
    </div>
  </>;
}
function KnowledgeWorkspace({ team, knowledgeBase }: { team: TeamSummary; knowledgeBase: KnowledgeBase }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => { setDocuments(await api.listDocuments(team.id, knowledgeBase.id)); }, [team.id, knowledgeBase.id]);
  const processing = documents.some((doc) => doc.status === "pending" || doc.status === "processing");
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      let shouldPoll = processing;
      try { const items = await api.listDocuments(team.id, knowledgeBase.id); shouldPoll = items.some((doc) => doc.status === "pending" || doc.status === "processing"); if (!cancelled) { setDocuments(items); } }
      catch (err) { if (!cancelled) setError(message(err, "加载资料失败，请重试")); }
      finally { if (!cancelled) { setLoading(false); if (shouldPoll) timer = setTimeout(() => void load(), 3000); } }
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [team.id, knowledgeBase.id, processing]);
  return <>
    {error && <p className="error-text" role="alert">{error}</p>}
    <div className="workspace__panels">
      <DocumentPanel teamId={team.id} knowledgeBaseId={knowledgeBase.id} documents={documents} loading={loading} canDelete={team.role === "admin"} onChanged={refresh} onError={setError} />
      <AskPanel teamId={team.id} knowledgeBaseId={knowledgeBase.id} canAsk={documents.some((doc) => doc.status === "ready")} processing={processing} onError={setError} />
    </div>
    <p className="workspace__footnote"><Icon name="quote" size={14} /> 回答供参考，请结合引用原文核对。</p>
  </>;
}
