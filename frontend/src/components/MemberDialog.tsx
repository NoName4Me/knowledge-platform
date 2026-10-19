import { useEffect, useState } from "react";
import { api, ApiError, type TeamMember } from "../lib/api";
import { Icon } from "./Icon";

type Props = {
  teamId: string;
  onClose: () => void;
  onError: (message: string) => void;
};

const message = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback;

export function MemberDialog({ teamId, onClose, onError }: Props) {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [removing, setRemoving] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listMembers(teamId)
      .then((items) => {
        if (!cancelled) setMembers(items);
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

  async function remove(member: TeamMember) {
    if (!window.confirm(`将 ${member.display_name} 移出团队？`)) return;
    setRemoving(member.user_id);
    try {
      await api.removeMember(teamId, member.user_id);
      setMembers((items) => items.filter((item) => item.user_id !== member.user_id));
    } catch (err) {
      onError(message(err, "移除成员失败"));
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
                  onClick={() => void remove(member)}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
