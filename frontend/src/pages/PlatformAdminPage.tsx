import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type PlatformTeam, type PlatformUser } from "../lib/api";
import { Brand, Icon } from "../components/Icon";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Modal } from "../components/Modal";
import { copyText } from "../lib/clipboard";
import { useToast } from "../lib/toast-context";

const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

export function PlatformAdminPage() {
  const toast = useToast();
  const [teams, setTeams] = useState<PlatformTeam[]>([]);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<PlatformUser | null>(null);
  const [assigningTeam, setAssigningTeam] = useState<PlatformTeam | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listPlatformTeams(), api.listPlatformUsers()])
      .then(([teamItems, userItems]) => {
        if (!cancelled) {
          setTeams(teamItems);
          setUsers(userItems);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          toast.notify(message(err, "加载平台管理数据失败"), { kind: "error" });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toast]);

  async function createTeam(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    try {
      const team = await api.createPlatformTeam(name.trim(), description.trim(), adminEmail.trim());
      setTeams((items) => [team, ...items]);
      setName("");
      setDescription("");
      setAdminEmail("");
      toast.notify("团队已创建", { kind: "success" });
    } catch (err) {
      toast.notify(message(err, "创建团队失败"), { kind: "error" });
    } finally {
      setCreating(false);
    }
  }

  async function toggleTeam(team: PlatformTeam) {
    try {
      const updated = await api.updatePlatformTeamStatus(team.id, !team.is_active);
      setTeams((items) => items.map((item) => (item.id === team.id ? updated : item)));
    } catch (err) {
      toast.notify(message(err, "更新团队状态失败"), { kind: "error" });
    }
  }

  async function copyAdministratorInvite(team: PlatformTeam) {
    if (!team.administrator_invitation_token) return;
    const link = `${window.location.origin}/invite/${team.administrator_invitation_token}`;
    const copied = await copyText(link);
    if (copied) {
      toast.notify("初始管理员邀请链接已复制", { kind: "success" });
    } else {
      toast.notify("浏览器阻止了复制，请手动选择链接复制", { kind: "error" });
    }
  }

  async function toggleUser(user: PlatformUser) {
    try {
      const updated = await api.updatePlatformUserStatus(user.id, !user.is_active);
      setUsers((items) => items.map((item) => (item.id === user.id ? updated : item)));
    } catch (err) {
      toast.notify(message(err, "更新账号状态失败"), { kind: "error" });
    }
  }

  async function resetPassword() {
    if (!resetting) return;
    try {
      const updated = await api.resetUserPassword(resetting.id);
      setUsers((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      toast.notify(`已重置 ${resetting.email} 的密码`, { kind: "success" });
    } catch (err) {
      throw new Error(message(err, "重置密码失败"));
    }
  }

  return (
    <div className="platform-admin">
      <header className="platform-admin__header">
        <a href="/" className="home-return">
          <Icon name="back" size={15} />
          工作台
        </a>
        <Brand />
      </header>
      <main className="platform-admin__main" tabIndex={-1}>
        <p className="member-dialog__eyebrow">平台管理</p>
        <h1>账号与团队</h1>
        <p className="platform-admin__intro">
          平台只管理账号、团队和负责人。团队资料与成员的私人内容仍在各自团队内受权限保护。
        </p>
        {loading ? (
          <p role="status">正在加载…</p>
        ) : (
          <>
            <section className="platform-admin__panel">
              <h2>新建团队</h2>
              <form className="platform-team-form" onSubmit={(event) => void createTeam(event)}>
                <label className="field">
                  <span>团队名称</span>
                  <input
                    required
                    maxLength={120}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label className="field">
                  <span>团队简介</span>
                  <input
                    maxLength={500}
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                  />
                </label>
                <label className="field">
                  <span>初始管理员邮箱</span>
                  <input
                    type="email"
                    required
                    value={adminEmail}
                    onChange={(event) => setAdminEmail(event.target.value)}
                    placeholder="已有账号直接加入，未注册账号生成邀请"
                  />
                </label>
                <button className="btn" disabled={creating || !name.trim() || !adminEmail.trim()}>
                  {creating ? "创建中…" : "创建团队"}
                </button>
              </form>
            </section>
            <section className="platform-admin__panel">
              <h2>团队 <span>{teams.length}</span></h2>
              <ul className="platform-admin__list">
                {teams.map((team) => (
                  <li key={team.id}>
                    <div>
                      <strong>{team.name}</strong>
                      <small>
                        {team.administrator_count} 名可登录管理员
                        {team.pending_administrator_invitation_count
                          ? ` · ${team.pending_administrator_invitation_count} 个管理员邀请待接受`
                          : ""}
                        {" · "}
                        {team.is_active ? "可用" : "已停用"}
                      </small>
                      {team.description && <p>{team.description}</p>}
                      {team.administrator_invitation_token && (
                        <div className="platform-admin__invite">
                          <input
                            aria-label={`${team.name} 初始管理员邀请链接`}
                            readOnly
                            value={`${window.location.origin}/invite/${team.administrator_invitation_token}`}
                            onFocus={(event) => event.currentTarget.select()}
                          />
                          <button
                            className="btn btn--ghost"
                            onClick={() => void copyAdministratorInvite(team)}
                          >
                            复制邀请链接
                          </button>
                        </div>
                      )}
                    </div>
                    <div className="platform-admin__actions">
                      {team.administrator_count === 0 && (
                        <button className="btn btn--ghost" onClick={() => setAssigningTeam(team)}>
                          指定管理员
                        </button>
                      )}
                      <button className="btn btn--ghost" onClick={() => void toggleTeam(team)}>
                        {team.is_active ? "停用" : "启用"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
            <section className="platform-admin__panel">
              <h2>账号 <span>{users.length}</span></h2>
              <ul className="platform-admin__list">
                {users.map((user) => (
                  <li key={user.id}>
                    <div>
                      <strong>{user.display_name}</strong>
                      <small>
                        {user.email} · {user.is_active ? "可用" : "已停用"}
                        {user.must_change_password ? " · 待改密" : ""}
                      </small>
                    </div>
                    <div className="platform-admin__actions">
                      <button className="btn btn--ghost" onClick={() => setResetting(user)}>
                        重置密码
                      </button>
                      <button className="btn btn--ghost" onClick={() => void toggleUser(user)}>
                        {user.is_active ? "停用" : "启用"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </main>
      {resetting && (
        <ConfirmDialog
          title={`重置 ${resetting.email} 的密码？`}
          confirmLabel="重置为 user1234"
          tone="danger"
          onClose={() => setResetting(null)}
          onConfirm={resetPassword}
          description={
            <>
              <p>该账号在所有团队的旧会话都会失效。</p>
              <p>用户下次登录必须先把临时密码修改为自己的密码。</p>
            </>
          }
        />
      )}
      {assigningTeam && (
        <AssignAdministratorDialog
          team={assigningTeam}
          onClose={() => setAssigningTeam(null)}
          onAssigned={(updated) => {
            setTeams((items) =>
              items.map((item) => (item.id === updated.id ? updated : item)),
            );
            setAssigningTeam(null);
          }}
          onError={(value) => toast.notify(value, { kind: "error" })}
        />
      )}
    </div>
  );
}

function AssignAdministratorDialog({
  team,
  onClose,
  onAssigned,
  onError,
}: {
  team: PlatformTeam;
  onClose: () => void;
  onAssigned: (team: PlatformTeam) => void;
  onError: (message: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const updated = await api.assignPlatformTeamAdministrator(team.id, email.trim());
      onAssigned(updated);
    } catch (err) {
      onError(message(err, "指定管理员失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`为「${team.name}」指定管理员`} busy={busy} onClose={onClose}>
      <form onSubmit={(event) => void submit(event)}>
        <p className="maintenance-hint">
          已有账号会直接加入团队；未注册账号会收到一条新的管理员邀请，旧邀请随即失效。
        </p>
        <label className="field">
          <span>管理员邮箱</span>
          <input
            type="email"
            required
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={busy}
          />
        </label>
        <div className="maintenance-actions">
          <button type="button" className="btn btn--ghost" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button className="btn" disabled={busy || !email.trim()}>
            {busy ? "处理中…" : "指定管理员"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
