import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type TeamInvitation, type TeamMember } from "../lib/api";
import { Icon } from "./Icon";
import { SelectMenu } from "./Popover";
import { ConfirmDialog } from "./ConfirmDialog";

type Props = {
  teamId: string;
  onClose: () => void;
  onError: (message: string) => void;
};

const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

export function MemberDialog({ teamId, onClose, onError }: Props) {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [invitations, setInvitations] = useState<TeamInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [removing, setRemoving] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [inviting, setInviting] = useState(false);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [removingMember, setRemovingMember] = useState<TeamMember | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listMembers(teamId), api.listInvitations(teamId)])
      .then(([memberItems, invitationItems]) => {
        if (!cancelled) {
          setMembers(memberItems);
          setInvitations(invitationItems.filter((item) => !item.accepted_at));
        }
      })
      .catch((err) => {
        if (!cancelled) onError(message(err, "加载成员失败"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [teamId, onError]);

  async function invite(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || inviting) return;
    setInviting(true);
    try {
      const result = await api.createInvitation(teamId, email.trim(), role);
      setInvitations((items) => [result.invitation, ...items]);
      setInviteLink(`${window.location.origin}/invite/${result.token}`);
      setEmail("");
    } catch (err) {
      onError(message(err, "创建邀请失败"));
    } finally {
      setInviting(false);
    }
  }

  async function copyInviteLink() {
    if (!inviteLink) return;
    try {
      await navigator.clipboard.writeText(inviteLink);
    } catch {
      onError("复制失败，请手动选择链接复制");
    }
  }

  async function revoke(invitation: TeamInvitation) {
    try {
      await api.revokeInvitation(teamId, invitation.id);
      setInvitations((items) => items.filter((item) => item.id !== invitation.id));
    } catch (err) {
      onError(message(err, "撤销邀请失败"));
    }
  }

  async function remove(member: TeamMember) {
    setRemoving(member.user_id);
    try {
      await api.removeMember(teamId, member.user_id);
      setMembers((items) => items.filter((item) => item.user_id !== member.user_id));
    } catch (err) {
      throw new Error(message(err, "移除成员失败"));
    } finally {
      setRemoving(null);
    }
  }

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="member-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="member-dialog-title"
      >
        <header className="member-dialog__head">
          <div>
            <p className="member-dialog__eyebrow">团队设置</p>
            <h2 id="member-dialog-title">成员</h2>
          </div>
          <button className="icon-button" aria-label="关闭成员设置" onClick={onClose}>
            <Icon name="plus" size={18} style={{ transform: "rotate(45deg)" }} />
          </button>
        </header>
        <p className="member-dialog__note">资料由团队共享；每位成员的问答历史仅本人可见。</p>
        <form className="invite-form" onSubmit={invite}>
          <div className="invite-form__heading">
            <strong>邀请成员</strong>
            <span>生成一次性链接，72 小时内有效。</span>
          </div>
          <div className="invite-form__row">
            <input
              type="email"
              aria-label="受邀邮箱"
              placeholder="member@example.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
            <SelectMenu
              className="invite-role-select"
              label="成员角色"
              value={role}
              onChange={(value) => setRole(value as "admin" | "member")}
              options={[
                { id: "member", label: "成员" },
                { id: "admin", label: "管理员" },
              ]}
            />
            <button className="btn" disabled={inviting || !email.trim()}>
              {inviting ? "生成中…" : "生成邀请"}
            </button>
          </div>
          {inviteLink && (
            <div className="invite-link" role="status">
              <span>{inviteLink}</span>
              <button type="button" className="btn btn--ghost" onClick={() => void copyInviteLink()}>
                复制链接
              </button>
            </div>
          )}
        </form>
        {loading ? (
          <p className="panel__empty" role="status">正在加载成员…</p>
        ) : (
          <ul className="member-list">
            {members.map((member) => (
              <li key={member.user_id} className="member-row">
                <span className="member-row__avatar">{member.display_name.slice(0, 1)}</span>
                <span className="member-row__main">
                  <strong>{member.display_name}</strong>
                  <small>{member.email}</small>
                </span>
                <span className="member-row__role">
                  {member.role === "admin" ? "管理员" : "成员"}
                </span>
                <button
                  className="btn btn--ghost member-row__remove"
                  disabled={removing !== null}
                  onClick={() => setRemovingMember(member)}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
        {invitations.length > 0 && (
          <section className="pending-invites" aria-labelledby="pending-invites-title">
            <h3 id="pending-invites-title">待接受邀请</h3>
            <ul>
              {invitations.map((invitation) => (
                <li key={invitation.id}>
                  <span>
                    <strong>{invitation.email}</strong>
                    <small>
                      {invitation.role === "admin" ? "管理员" : "成员"} ·{" "}
                      {new Date(invitation.expires_at).toLocaleDateString("zh-CN")} 到期
                    </small>
                  </span>
                  <button className="btn btn--ghost member-row__remove" onClick={() => void revoke(invitation)}>
                    撤销
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {removingMember && (
          <ConfirmDialog
            title={`将 ${removingMember.display_name} 移出团队？`}
            confirmLabel="移出团队"
            tone="danger"
            onClose={() => setRemovingMember(null)}
            onConfirm={() => remove(removingMember)}
            description={<>
              <p>{removingMember.display_name} 将无法继续访问本团队的资料、问答与分析。</p>
              <p>其已创建的私人问答和分析记录会保留，但本人不再可见。</p>
            </>}
          />
        )}
      </section>
    </div>
  );
}
