import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, ApiError, type TeamSummary } from "../lib/api";
import { teamRoleLabel } from "../lib/team-role";
import { Icon } from "./Icon";
import { Modal } from "./Modal";

type Props = {
  team: TeamSummary;
  onClose: () => void;
  onManageMembers: () => void;
  onError: (message: string) => void;
  onSuccess: (message: string) => void;
};

const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

const rolePermissions = {
  viewer: "使用问答、分析、自动成稿、笔记和报告；团队知识库默认只读，私人知识库按创建者授权。",
  member: "可使用全部查阅能力；团队知识库默认可编辑，私人知识库按创建者授权。",
  admin: "管理团队信息和成员；团队知识库默认可管理，但不会自动访问他人的私人知识库。",
} as const;

export function TeamSettingsDialog({
  team,
  onClose,
  onManageMembers,
  onError,
  onSuccess,
}: Props) {
  const { refresh } = useAuth();
  const [details, setDetails] = useState<TeamSummary>(team);
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description ?? "");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const isAdmin = team.role === "admin";

  useEffect(() => {
    let cancelled = false;
    api
      .getTeam(team.id)
      .then((value) => {
        if (cancelled) return;
        setDetails(value);
        setName(value.name);
        setDescription(value.description ?? "");
      })
      .catch((err) => {
        if (!cancelled) onError(message(err, "加载团队设置失败"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [team.id, onError]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    try {
      const updated = await api.updateTeam(team.id, {
        name: name.trim(),
        description: description.trim() || null,
      });
      setDetails(updated);
      await refresh();
      onSuccess("团队信息已更新");
    } catch (err) {
      onError(message(err, "更新团队信息失败"));
    } finally {
      setSaving(false);
    }
  }

  const visibleRoles = isAdmin
    ? (["viewer", "member", "admin"] as const)
    : ([details.role] as const);

  return (
    <Modal title={`${details.name} · 团队设置`} busy={saving} onClose={onClose}>
      <div className="team-settings">
        <div className="team-settings__summary">
          <span className="team-emblem">{details.name.slice(0, 1)}</span>
          <div>
            <strong>{details.name}</strong>
            <small>你的角色：{teamRoleLabel(details.role)}</small>
          </div>
        </div>
        {isAdmin ? (
          <form className="team-settings__form" onSubmit={(event) => void save(event)}>
            <label className="field">
              <span>团队名称</span>
              <input
                required
                maxLength={120}
                value={name}
                disabled={loading || saving}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="field">
              <span>团队简介</span>
              <textarea
                maxLength={500}
                rows={3}
                value={description}
                disabled={loading || saving}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="说明团队共享哪些资料"
              />
            </label>
            <div className="maintenance-actions team-settings__save">
              <button className="btn" disabled={loading || saving || !name.trim()}>
                {saving ? "保存中…" : "保存团队信息"}
              </button>
            </div>
          </form>
        ) : (
          details.description && <p className="team-settings__description">{details.description}</p>
        )}
        <section className="team-settings__roles" aria-labelledby="team-role-permissions">
          <h3 id="team-role-permissions">权限说明</h3>
          <ul>
            {visibleRoles.map((role) => (
              <li key={role}>
                <strong>{teamRoleLabel(role)}</strong>
                <p>{rolePermissions[role]}</p>
              </li>
            ))}
          </ul>
        </section>
        {isAdmin && (
          <button
            type="button"
            className="btn btn--ghost team-settings__members"
            onClick={onManageMembers}
          >
            <Icon name="user" size={16} />
            管理成员与邀请
          </button>
        )}
      </div>
    </Modal>
  );
}
