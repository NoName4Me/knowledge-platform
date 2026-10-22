import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "./Icon";
import { PopoverMenu } from "./Popover";
import { ProfileDialog } from "./ProfileDialog";
import { useToast } from "../lib/toast-context";

export function ProfileMenu() {
  const { session, logout } = useAuth();
  const toast = useToast();
  const [profile, setProfile] = useState(false);
  const [error, setError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  if (!session) return null;
  return <>
    <PopoverMenu label={`${session.user.display_name}的个人菜单`} className="profile-menu" trigger={<><span className="profile-avatar">{session.user.display_name.slice(0, 1)}</span><span className="profile-menu__name">{session.user.display_name}</span><Icon name="chevron" size={15} /></>}>
      {(close) => <>
        <p className="popover__meta">{session.user.email}</p>
        <button type="button" role="menuitem" onClick={() => { close(); setProfile(true); }}><Icon name="user" size={16} />个人资料</button>
        {session.user.is_platform_admin && <a role="menuitem" href="/platform"><Icon name="shield" size={16} />平台管理</a>}
        <button type="button" role="menuitem" disabled={loggingOut} onClick={() => {
          setLoggingOut(true); setError("");
          void logout().catch((err) => setError(err instanceof Error ? err.message : "退出失败，请重试"))
            .finally(() => setLoggingOut(false));
        }}><Icon name="logout" size={16} />{loggingOut ? "正在退出…" : "退出登录"}</button>
        {error && <p className="error-text" role="alert">{error}</p>}
      </>}
    </PopoverMenu>
    {profile && <ProfileDialog
      onClose={() => setProfile(false)}
      onError={(value) => toast.notify(value, { kind: "error" })}
      onSuccess={(value) => toast.notify(value, { kind: "success" })}
      onPasswordChanged={async () => window.location.assign("/")}
    />}
  </>;
}
