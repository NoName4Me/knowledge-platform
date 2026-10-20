import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { PopoverMenu } from "./Popover";

export function ProfileMenu() {
  const { session, logout } = useAuth();
  const [profile, setProfile] = useState(false);
  const [error, setError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  if (!session) return null;
  return <>
    <PopoverMenu label={`${session.user.display_name}的个人菜单`} className="profile-menu" trigger={<><span className="profile-avatar">{session.user.display_name.slice(0, 1)}</span><span className="profile-menu__name">{session.user.display_name}</span><Icon name="chevron" size={15} /></>}>
      {(close) => <>
        <p className="popover__meta">{session.user.email}</p>
        <button type="button" role="menuitem" onClick={() => { close(); setProfile(true); }}><Icon name="user" size={16} />个人资料</button>
        <button type="button" role="menuitem" disabled={loggingOut} onClick={() => {
          setLoggingOut(true); setError("");
          void logout().catch((err) => setError(err instanceof Error ? err.message : "退出失败，请重试"))
            .finally(() => setLoggingOut(false));
        }}><Icon name="logout" size={16} />{loggingOut ? "正在退出…" : "退出登录"}</button>
        {error && <p className="error-text" role="alert">{error}</p>}
      </>}
    </PopoverMenu>
    {profile && <Modal title="个人资料" onClose={() => setProfile(false)}>
      <div className="profile-detail"><span className="profile-avatar">{session.user.display_name.slice(0, 1)}</span><div><h3>{session.user.display_name}</h3><p>{session.user.email}</p></div></div>
      <h3 className="profile-teams-title">已加入的团队</h3><ul className="profile-team-list">{session.teams.map((team) => <li key={team.id}><span>{team.name}</span><small>{team.role === "admin" ? "管理员" : "团队成员"}</small></li>)}</ul>
      <div className="maintenance-actions"><button className="btn btn--ghost" type="button" onClick={() => setProfile(false)}>关闭</button></div>
    </Modal>}
  </>;
}
