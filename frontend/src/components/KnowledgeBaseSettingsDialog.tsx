import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type KnowledgeBase,
  type KnowledgeBaseMember,
} from "../lib/api";
import { Modal } from "./Modal";
import { SelectMenu } from "./Popover";
import { Icon } from "./Icon";

type Permission = KnowledgeBase["permission"];
type Override = Permission | "inherit" | "none";
type Props = {
  library: KnowledgeBase & { team: { id: string; name: string } };
  onClose: () => void;
  onSaved: (library: KnowledgeBase) => void;
  onSuccess: (message: string) => void;
};

const permissionRank: Record<Permission, number> = {
  view: 1,
  edit: 2,
  manage: 3,
};
const permissionLabel: Record<Permission, string> = {
  view: "只读",
  edit: "编辑",
  manage: "管理",
};
const teamRoleLabel = {
  admin: "团队管理员",
  member: "普通成员",
  viewer: "查阅成员",
} as const;
const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

function inheritedPermission(
  member: KnowledgeBaseMember,
  teamPermission: KnowledgeBase["team_permission"],
): Permission {
  if (member.team_role === "admin") return "manage";
  if (teamPermission === "inherit") {
    return member.team_role === "member" ? "edit" : "view";
  }
  return teamPermission;
}

function sourceLabel(member: KnowledgeBaseMember, privateLibrary: boolean) {
  if (member.source === "owner") return "创建者，固定管理";
  if (member.source === "team_role") return "继承团队角色";
  if (member.source === "team_default") return "团队默认权限";
  if (member.source === "explicit") {
    return privateLibrary ? "单独授权" : "高于团队默认";
  }
  return privateLibrary ? "未授权" : "无";
}

export function KnowledgeBaseSettingsDialog({
  library,
  onClose,
  onSaved,
  onSuccess,
}: Props) {
  const [name, setName] = useState(library.name);
  const [visibility, setVisibility] = useState(library.visibility);
  const [teamPermission, setTeamPermission] = useState(
    library.team_permission,
  );
  const [members, setMembers] = useState<KnowledgeBaseMember[]>([]);
  const [overrides, setOverrides] = useState<Record<string, Override>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .listKnowledgeBaseAccess(library.team.id, library.id)
      .then((rows) => {
        if (cancelled) return;
        setMembers(rows);
        setOverrides(
          Object.fromEntries(
            rows.map((member) => [
              member.user_id,
              member.explicit_permission ??
                (library.visibility === "private" ? "none" : "inherit"),
            ]),
          ),
        );
      })
      .catch((err) => {
        if (!cancelled) {
          setError(message(err, "加载知识库成员权限失败"));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [library.id, library.team.id, library.visibility]);

  function changeVisibility(value: KnowledgeBase["visibility"]) {
    setVisibility(value);
    setOverrides((current) =>
      Object.fromEntries(
        Object.entries(current).map(([userId, permission]) => [
          userId,
          value === "private" && permission === "inherit"
            ? "none"
            : value === "team" && permission === "none"
              ? "inherit"
              : permission,
        ]),
      ),
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    setError("");
    try {
      const updated = await api.updateKnowledgeBase(
        library.team.id,
        library.id,
        {
          name: name.trim(),
          visibility,
          team_permission:
            visibility === "private" ? "inherit" : teamPermission,
        },
      );
      const accessRows = members.flatMap((member) => {
        const override = overrides[member.user_id] ?? "inherit";
        if (member.user_id === library.owner_id) return [];
        if (visibility === "team" && member.team_role === "admin") return [];
        if (visibility === "team") {
          if (override === "inherit" || override === "none") return [];
          const inherited = inheritedPermission(member, teamPermission);
          if (permissionRank[override] <= permissionRank[inherited]) return [];
        } else if (override === "inherit" || override === "none") {
          return [];
        }
        return [{ user_id: member.user_id, permission: override as Permission }];
      });
      await api.replaceKnowledgeBaseAccess(
        library.team.id,
        library.id,
        accessRows,
      );
      onSaved(updated);
      onSuccess("知识库设置和成员权限已更新");
      onClose();
    } catch (err) {
      setError(message(err, "保存知识库设置失败"));
    } finally {
      setSaving(false);
    }
  }

  const privateLibrary = visibility === "private";
  return (
    <Modal
      title={`${library.name} · 知识库设置`}
      busy={saving}
      onClose={onClose}
    >
      <form className="kb-settings" onSubmit={(event) => void save(event)}>
        <div className="kb-settings__intro">
          <span className="library-emblem">
            <Icon name={privateLibrary ? "lock" : "book"} size={22} />
          </span>
          <div>
            <strong>{privateLibrary ? "私人知识库" : "团队知识库"}</strong>
            <p>
              {privateLibrary
                ? "只有创建者和下方明确授权的成员可以访问。"
                : "团队成员按默认权限访问，并可给个人提高权限。"}
            </p>
          </div>
        </div>

        <label className="field">
          <span>知识库名称</span>
          <input
            autoFocus
            required
            maxLength={200}
            value={name}
            disabled={saving}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <div className="field">
          <span>可见范围</span>
          <SelectMenu
            label="知识库可见范围"
            value={visibility}
            disabled={saving}
            onChange={(value) =>
              changeVisibility(value as KnowledgeBase["visibility"])
            }
            options={[
              { id: "team", label: "团队可见" },
              { id: "private", label: "仅创建者和获授权成员" },
            ]}
          />
        </div>

        {!privateLibrary && (
          <div className="field">
            <span>团队成员默认权限</span>
            <SelectMenu
              label="团队成员默认权限"
              value={teamPermission}
              disabled={saving}
              onChange={(value) =>
                setTeamPermission(value as KnowledgeBase["team_permission"])
              }
              options={[
                { id: "inherit", label: "继承团队角色" },
                { id: "view", label: "所有成员只读" },
                { id: "edit", label: "所有成员可编辑" },
                { id: "manage", label: "所有成员可管理" },
              ]}
            />
            <small className="kb-settings__hint">
              继承团队角色时，查阅成员只读、普通成员可编辑、团队管理员可管理。
              管理员和创建者始终可管理。
            </small>
          </div>
        )}

        <section
          className="kb-settings__members"
          aria-labelledby="kb-settings-members-title"
        >
          <div className="kb-settings__section-head">
            <div>
              <h3 id="kb-settings-members-title">成员权限</h3>
              <p>
                {privateLibrary
                  ? "逐个授权团队成员；撤权后其会话、深链接和后台成果发布同时失效。"
                  : "个人设置只能提高权限，不能低于团队默认。"}
              </p>
            </div>
            <span>{members.length} 名成员</span>
          </div>
          {loading ? (
            <p className="panel__empty" role="status">
              正在加载成员权限…
            </p>
          ) : (
            <ul className="kb-access-list">
              {members.map((member) => {
                const fixed =
                  member.user_id === library.owner_id ||
                  (!privateLibrary && member.team_role === "admin");
                const inherited = inheritedPermission(member, teamPermission);
                const options = privateLibrary
                  ? [
                      { id: "none", label: "无访问" },
                      { id: "view", label: "只读" },
                      { id: "edit", label: "可编辑" },
                      { id: "manage", label: "可管理" },
                    ]
                  : [
                      { id: "inherit", label: "继承团队默认" },
                      ...(["view", "edit", "manage"] as const)
                        .filter(
                          (permission) =>
                            permissionRank[permission] >
                            permissionRank[inherited],
                        )
                        .map((permission) => ({
                          id: permission,
                          label: `提升为${permissionLabel[permission]}`,
                        })),
                    ];
                return (
                  <li key={member.user_id}>
                    <span className="member-row__avatar">
                      {member.display_name.slice(0, 1)}
                    </span>
                    <span className="kb-access-list__identity">
                      <strong>{member.display_name}</strong>
                      <small>
                        {member.email} · {teamRoleLabel[member.team_role]}
                      </small>
                    </span>
                    <span className="kb-access-list__source">
                      {fixed
                        ? "创建者 / 团队管理员，固定管理"
                        : sourceLabel(member, privateLibrary)}
                    </span>
                    {fixed ? (
                      <span className="kb-access-list__fixed">可管理</span>
                    ) : (
                      <SelectMenu
                        className="kb-access-list__select"
                        label={`${member.display_name}的知识库权限`}
                        value={overrides[member.user_id] ?? "inherit"}
                        disabled={saving}
                        onChange={(value) =>
                          setOverrides((current) => ({
                            ...current,
                            [member.user_id]: value as Override,
                          }))
                        }
                        options={options}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <div className="maintenance-actions">
          <button
            type="button"
            className="btn btn--ghost"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="btn"
            disabled={saving || loading || !name.trim()}
          >
            {saving ? "保存中…" : "保存设置"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
