import type { TeamSummary } from "../lib/api";
import { Brand, Icon } from "./Icon";
import { teamRoleLabel } from "../lib/team-role";
type Props = { teams: TeamSummary[]; activeTeamId: string | null; onSelect: (id: string) => void; userName: string; onLogout: () => void; };
export function TeamSidebar({ teams, activeTeamId, onSelect, userName, onLogout }: Props) {
  return <aside className="workspace__sidebar" aria-label="团队导航">
    <Brand />
    <div className="sidebar-section"><h2 className="sidebar-label">我的团队 <span>{teams.length}</span></h2>
      <ul className="team-list">{teams.map((team) => <li key={team.id}><button className={`team-item${team.id === activeTeamId ? " team-item--active" : ""}`} onClick={() => onSelect(team.id)} aria-current={team.id === activeTeamId ? "true" : undefined}><span className="team-item__avatar">{team.name.slice(0, 1)}</span><span className="team-item__body"><span className="team-item__name">{team.name}</span><span className="team-item__role">{teamRoleLabel(team.role)}</span></span>{team.id === activeTeamId && <span className="team-item__dot" />}</button></li>)}</ul>
    </div>
    <div className="sidebar-note"><Icon name="shield" size={18} /><p>当前团队的资料<br />仅供团队成员访问</p></div>
    <div className="sidebar-user"><span className="user-avatar">{userName.slice(0, 1)}</span><span className="sidebar-user__name">{userName}</span><button className="icon-button" aria-label="退出登录" title="退出登录" onClick={onLogout}><Icon name="logout" size={18} /></button></div>
  </aside>;
}
