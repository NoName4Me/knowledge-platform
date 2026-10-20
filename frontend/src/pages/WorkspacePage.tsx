import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, ApiError, type KnowledgeBase, type TeamSummary } from "../lib/api";
import { useToast } from "../lib/toast-context";
import { knowledgeBasePath, workspaceRoute } from "../lib/workspace-route";
import { readPreference, writePreference } from "../lib/workspace-preferences";
import { MemberDialog } from "../components/MemberDialog";
import { NameDialog } from "../components/NameDialog";
import { ProfileMenu } from "../components/ProfileMenu";
import { Modal } from "../components/Modal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Brand, Icon } from "../components/Icon";
import { PopoverMenu, SelectMenu } from "../components/Popover";
import { KnowledgeWorkspace } from "./KnowledgeWorkspace";

export type Library = KnowledgeBase & { team: TeamSummary };
const message = (error: unknown, fallback: string) => error instanceof ApiError ? error.message : fallback;

function CreateLibrary({ teams, initialTeam, onClose, onCreated }: {
  teams: TeamSummary[]; initialTeam: string; onClose: () => void; onCreated: (library: Library) => void;
}) {
  const [teamId, setTeamId] = useState(teams.some((team) => team.id === initialTeam) ? initialTeam : teams[0]?.id ?? "");
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    const team = teams.find((item) => item.id === teamId);
    if (!team || !name.trim() || saving) return;
    setSaving(true); setError("");
    try { const kb = await api.createKnowledgeBase(teamId, name.trim()); onCreated({ ...kb, team }); }
    catch (err) { setError(message(err, "创建知识库失败，请重试")); }
    finally { setSaving(false); }
  }
  return <Modal title="新建知识库" busy={saving} onClose={onClose}><form onSubmit={(event) => void create(event)}>
    <div className="field"><span>所属团队</span><SelectMenu label="所属团队" value={teamId} onChange={setTeamId} disabled={saving} options={teams.map((team) => ({ id: team.id, label: team.name }))} /></div>
    <label className="field"><span>知识库名称</span><input autoFocus required maxLength={200} placeholder="例如：产品资料、团队制度" value={name} onChange={(event) => setName(event.target.value)} disabled={saving} /></label>
    {error && <p role="alert" className="error-text">{error}</p>}
    <div className="maintenance-actions"><button type="button" className="btn btn--ghost" disabled={saving} onClick={onClose}>取消</button><button className="btn" disabled={saving || !name.trim() || !teamId}>{saving ? "创建中…" : "创建并进入"}</button></div>
  </form></Modal>;
}

function LibraryMenu({ library, onRename, onDelete, busy }: {
  library: Library; onRename: () => void; onDelete: () => void; busy: boolean;
}) {
  return <PopoverMenu label={`管理知识库 ${library.name}`} className="library-menu" trigger={<Icon name="more" size={20} />}>
    {(close) => <>
      <button type="button" role="menuitem" disabled={busy} onClick={() => { close(); onRename(); }}>重命名知识库</button>
      <button type="button" role="menuitem" className="is-danger" disabled={busy} onClick={() => { close(); onDelete(); }}>删除知识库</button>
    </>}
  </PopoverMenu>;
}

export function WorkspacePage() {
  const { session } = useAuth();
  const toast = useToast();
  const route = workspaceRoute(window.location.pathname);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [loading, setLoading] = useState(true);
  const [teamErrors, setTeamErrors] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<Library | null>(null);
  const [managingTeam, setManagingTeam] = useState<TeamSummary | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Library | null>(null);
  const [teamFilter, setTeamFilter] = useState("all");
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    Promise.allSettled(session.teams.map((team) => api.listKnowledgeBases(team.id))).then((results) => {
      if (cancelled) return;
      const items: Library[] = []; const errors: Record<string, string> = {};
      results.forEach((result, index) => {
        const team = session.teams[index];
        if (result.status === "fulfilled") items.push(...result.value.map((kb) => ({ ...kb, team })));
        else errors[team.id] = message(result.reason, "知识库加载失败，请重试");
      });
      setLibraries(items); setTeamErrors(errors); setLoading(false);
    });
    return () => { cancelled = true; };
  }, [session, revision]);
  if (!session) return null;
  const active = route.kind === "knowledge" ? libraries.find((kb) => kb.id === route.knowledgeBaseId && kb.team.id === route.teamId) : undefined;
  const adminTeams = session.teams.filter((team) => team.role === "admin");
  const visible = libraries.filter((kb) => (teamFilter === "all" || kb.team.id === teamFilter) && `${kb.name} ${kb.team.name}`.toLowerCase().includes(query.toLowerCase()));
  const lastTeam = readPreference(`${session.user.id}:team`);
  function enter(kb: Library) {
    writePreference(`${session!.user.id}:team`, kb.team.id);
    writePreference(`${session!.user.id}:${kb.team.id}:knowledge-base`, kb.id);
    window.location.assign(knowledgeBasePath(kb.team.id, kb.id));
  }
  function reload() { setLoading(true); setRevision((value) => value + 1); }
  async function deleteLibrary(kb: Library) {
    if (deleting) return;
    setDeleting(kb.id);
    try {
      await api.deleteKnowledgeBase(kb.team.id, kb.id);
      toast.notify(`已删除知识库“${kb.name}”`, { kind: "success" });
      if (active?.id === kb.id) window.location.assign("/");
      else setLibraries((items) => items.filter((item) => item.id !== kb.id));
    } catch (err) {
      // 交给确认弹窗展示，用户可以在原地重试或取消。
      throw new Error(message(err, "删除知识库失败，请重试"));
    }
    finally { setDeleting(null); }
  }
  const management = (kb: Library) => kb.team.role === "admin" ? <LibraryMenu library={kb} busy={deleting !== null} onRename={() => setRenaming(kb)} onDelete={() => setConfirming(kb)} /> : null;
  return <div className="workbench">
    <a className="skip-link" href="#workspace-main">跳到工作区</a>
    <header className="workbench-header">
      {route.kind === "home" ? <a className="workbench-brand" href="/" aria-label="知识工作台首页"><Brand /></a> : <div className="library-heading"><a className="home-return" href="/" aria-label="返回首页" title="返回首页"><Icon name="back" size={19} /></a><span className="header-separator" />
        <div className="library-switch"><SelectMenu
          className="library-switch__control"
          label="切换知识库"
          value={active?.id ?? ""}
          disabled={loading || !active}
          placeholder={loading ? "正在加载知识库…" : "知识库不可访问"}
          options={libraries.map((kb) => ({ id: kb.id, label: kb.name, group: kb.team.name }))}
          onChange={(id) => { const kb = libraries.find((item) => item.id === id); if (kb) enter(kb); }}
        /><small>{active?.team.name ?? ""}</small></div>{active && management(active)}</div>}
      <ProfileMenu />
    </header>
    {route.kind === "home" ? <main id="workspace-main" className="home-page" tabIndex={-1}>
      <div className="home-intro"><div><h1>我的工作台</h1><p>从一份资料开始，把问题带进知识库。</p></div>{adminTeams.length > 0 && <button className="btn" type="button" onClick={() => setCreating(true)}><Icon name="plus" size={17} />新建知识库</button>}</div>
      <div className="home-layout"><section className="home-libraries" aria-labelledby="home-libraries-title">
        <div className="home-section-heading"><h2 id="home-libraries-title">知识库 <span>{libraries.length}</span></h2><div className="home-filters"><SelectMenu className="home-filter-select" label="按团队筛选" value={teamFilter} onChange={setTeamFilter} options={[{ id: "all", label: "全部团队" }, ...session.teams.map((team) => ({ id: team.id, label: team.name }))]} /><label className="sr-only" htmlFor="library-search">搜索知识库</label><input id="library-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索知识库" /></div></div>
        {Object.entries(teamErrors).map(([id, value]) => <p className="error-text home-load-error" key={id} role="alert">{session.teams.find((team) => team.id === id)?.name}：{value}<button type="button" className="btn btn--ghost" onClick={reload}>重试</button></p>)}
        {loading ? <p className="home-empty" role="status">正在加载知识库…</p> : visible.length ? <div className="library-grid">{visible.map((kb) => <article className="library-card" key={kb.id}><div className="library-card__top"><span className="library-emblem"><Icon name="book" size={24} /></span>{management(kb)}</div><a className="library-card__link" href={knowledgeBasePath(kb.team.id, kb.id)}><h3>{kb.name}</h3><p>{kb.team.name}</p><span>进入知识库 <Icon name="external" size={13} /></span></a></article>)}</div> : <div className="home-empty"><Icon name="book" size={30} /><h3>{query ? "没有匹配的知识库" : "还没有知识库"}</h3><p>{query ? "换个名称或团队试试。" : adminTeams.length ? "创建知识库，上传资料后即可开始问答与分析。" : "管理员创建知识库后，会显示在这里。"}</p></div>}
      </section><section className="home-teams" aria-labelledby="home-teams-title"><div className="home-section-heading"><h2 id="home-teams-title">团队 <span>{session.teams.length}</span></h2></div>
        <ul>{session.teams.map((team) => <li key={team.id}><span className="team-emblem">{team.name.slice(0, 1)}</span><div><h3>{team.name}</h3><p>{team.role === "admin" ? "管理员" : "团队成员"}</p><div className="home-team-actions"><button type="button" onClick={() => setTeamFilter(team.id)}>查看知识库</button>{team.role === "admin" && <button type="button" onClick={() => setManagingTeam(team)}>管理成员</button>}</div></div></li>)}</ul>{session.teams.length === 0 && <p className="home-empty">尚未加入团队，请联系管理员获取邀请。</p>}
      </section></div>
    </main> : loading ? <main className="workbench-loading" id="workspace-main" role="status">正在进入知识库…</main> : active ? <KnowledgeWorkspace key={`${active.team.id}:${active.id}`} library={active} /> : <main className="workbench-loading" id="workspace-main"><Icon name="book" size={32} /><h1>无法进入这个知识库</h1><p>{route.kind === "knowledge" && teamErrors[route.teamId] ? teamErrors[route.teamId] : "知识库不存在、已删除，或你已不属于对应团队。"}</p><div><a className="btn btn--ghost" href="/">返回首页</a><button className="btn" type="button" onClick={reload}>重新加载</button></div></main>}
    {creating && <CreateLibrary teams={adminTeams} initialTeam={teamFilter !== "all" ? teamFilter : lastTeam} onClose={() => setCreating(false)} onCreated={enter} />}
    {renaming && <NameDialog title="重命名知识库" label="知识库名称" initial={renaming.name} onClose={() => setRenaming(null)} onSave={async (name) => { const updated = await api.renameKnowledgeBase(renaming.team.id, renaming.id, name); setLibraries((items) => items.map((kb) => kb.id === updated.id ? { ...kb, ...updated } : kb)); }} />}
    {confirming && <ConfirmDialog
      title={`删除知识库“${confirming.name}”？`}
      confirmLabel="永久删除"
      tone="danger"
      onClose={() => setConfirming(null)}
      onConfirm={() => deleteLibrary(confirming)}
      description={<>
        <p>这是不可恢复的操作，删除后会一并清理：</p>
        <ul>
          <li>该知识库内的全部资料与原始文件</li>
          <li>目录结构，以及资料与目录的归属</li>
          <li>所有成员的问答记录与引用依据</li>
          <li>全部分析任务、字段提取与对比结论</li>
        </ul>
        <p>团队存储额度会同步释放；其他知识库不受影响。</p>
      </>}
    />}
    {managingTeam && <MemberDialog teamId={managingTeam.id} onClose={() => setManagingTeam(null)} onError={(value) => toast.notify(value, { kind: "error" })} />}
  </div>;
}
