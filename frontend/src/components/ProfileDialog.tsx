import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, ApiError } from "../lib/api";
import { teamRoleLabel } from "../lib/team-role";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { ConfirmDialog } from "./ConfirmDialog";

type Props = {
  forced?: boolean;
  onClose: () => void;
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
  onPasswordChanged: () => Promise<void>;
};

const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

export function ProfileDialog({
  forced = false,
  onClose,
  onError,
  onSuccess,
  onPasswordChanged,
}: Props) {
  const { session, refresh } = useAuth();
  const [name, setName] = useState(session?.user.display_name ?? "");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [leavingTeam, setLeavingTeam] = useState<string | null>(null);
  if (!session) return null;

  async function saveName(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.updateProfile(name.trim());
      await refresh();
      onSuccess("显示名称已更新");
    } catch (err) {
      onError(message(err, "更新个人资料失败"));
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      await onPasswordChanged();
    } catch (err) {
      onError(message(err, "修改密码失败"));
    } finally {
      setBusy(false);
    }
  }

  async function leaveTeam(teamId: string) {
    try {
      await api.leaveTeam(teamId);
      await refresh();
      onSuccess("已退出团队");
      onClose();
      if (window.location.pathname.includes(`/teams/${teamId}/`)) {
        window.location.assign("/");
      }
    } catch (err) {
      throw new Error(message(err, "退出团队失败"));
    }
  }

  return (
    <Modal
      title={forced ? "请修改临时密码" : "个人资料"}
      busy={busy}
      closeable={!forced}
      onClose={forced ? () => undefined : onClose}
    >
      {forced ? (
        <p className="maintenance-hint">
          当前账号使用平台设置的临时密码。修改密码后才能继续使用工作台。
        </p>
      ) : (
        <>
          <div className="profile-detail">
            <span className="profile-avatar">{session.user.display_name.slice(0, 1)}</span>
            <div>
              <h3>{session.user.display_name}</h3>
              <p>{session.user.email}</p>
            </div>
          </div>
          <form onSubmit={(event) => void saveName(event)}>
            <div className="field">
              <label htmlFor="profile-name">显示名称</label>
              <input
                id="profile-name"
                value={name}
                maxLength={120}
                required
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="maintenance-actions">
              <button className="btn btn--ghost" disabled={busy || !name.trim()}>
                保存名称
              </button>
            </div>
          </form>
          <h3 className="profile-teams-title">所属团队</h3>
          <ul className="profile-team-list">
            {session.teams.map((team) => (
              <li key={team.id}>
                <span>
                  {team.name}
                  <small>{teamRoleLabel(team.role)}</small>
                </span>
                <button
                  type="button"
                  className="btn btn--ghost profile-team-list__leave"
                  onClick={() => setLeavingTeam(team.id)}
                >
                  退出团队
                </button>
              </li>
            ))}
          </ul>
          {session.user.is_platform_admin && (
            <p className="maintenance-hint">
              <Icon name="shield" size={15} /> 你同时拥有平台管理员身份。
            </p>
          )}
        </>
      )}
      <form onSubmit={(event) => void changePassword(event)}>
        <h3 className="profile-teams-title">修改密码</h3>
        <div className="field">
          <label htmlFor="profile-current-password">当前密码</label>
          <input
            id="profile-current-password"
            type="password"
            autoComplete="current-password"
            minLength={8}
            required
            autoFocus={forced}
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="profile-new-password">新密码</label>
          <input
            id="profile-new-password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
        </div>
        <div className="maintenance-actions">
          {!forced && (
            <button type="button" className="btn btn--ghost" onClick={onClose}>
              取消
            </button>
          )}
          <button className="btn" disabled={busy || !currentPassword || !newPassword}>
            {busy ? "正在保存…" : "修改密码"}
          </button>
        </div>
      </form>
      {leavingTeam && (() => {
        const team = session.teams.find((item) => item.id === leavingTeam);
        return team ? (
          <ConfirmDialog
            title={`退出「${team.name}」？`}
            confirmLabel="退出团队"
            tone="danger"
            onClose={() => setLeavingTeam(null)}
            onConfirm={() => leaveTeam(team.id)}
            description={
              <>
                <p>退出后将无法继续访问该团队的资料、问答与分析。</p>
                <p>团队公共资料和你的个人记录不会被删除。</p>
              </>
            }
          />
        ) : null;
      })()}
    </Modal>
  );
}
