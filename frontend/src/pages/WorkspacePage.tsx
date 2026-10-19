import { useEffect, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { api, type KnowledgeBase } from "../lib/api";

type LoadedKnowledgeBases = {
  teamId: string;
  items: KnowledgeBase[];
};

export function WorkspacePage() {
  const { session, logout } = useAuth();
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<LoadedKnowledgeBases | null>(null);

  const teams = session?.teams ?? [];
  const activeTeam = teams.find((team) => team.id === selectedTeamId) ?? teams[0] ?? null;
  const knowledgeBases = loaded?.teamId === activeTeam?.id ? loaded.items : null;

  useEffect(() => {
    if (!activeTeam) return;
    let cancelled = false;
    api
      .listKnowledgeBases(activeTeam.id)
      .then((items) => {
        if (!cancelled) setLoaded({ teamId: activeTeam.id, items });
      })
      .catch(() => {
        if (!cancelled) setLoaded({ teamId: activeTeam.id, items: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [activeTeam]);

  if (!session) return null;

  return (
    <div className="workspace">
      <header className="workspace__bar">
        <span className="display workspace__mark">知识工作台</span>
        <div className="workspace__identity">
          <span>{session.user.display_name}</span>
          <button className="btn btn--ghost workspace__logout" onClick={() => logout()}>
            退出
          </button>
        </div>
      </header>

      <div className="workspace__body">
        <aside className="workspace__teams" aria-label="团队">
          <h2 className="eyebrow-free-label">团队</h2>
          <ul>
            {teams.map((team) => (
              <li key={team.id}>
                <button
                  className={
                    team.id === activeTeam?.id ? "team-item team-item--active" : "team-item"
                  }
                  onClick={() => setSelectedTeamId(team.id)}
                  aria-current={team.id === activeTeam?.id}
                >
                  <span className="team-item__name">{team.name}</span>
                  <span className="team-item__role">
                    {team.role === "admin" ? "管理员" : "成员"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="workspace__main">
          <h1 className="display workspace__title">{activeTeam?.name}</h1>
          <p className="workspace__note">
            当前团队的知识库。检索与问答将在下一阶段接入，这里已按团队隔离。
          </p>

          <section aria-labelledby="kb-heading">
            <h2 id="kb-heading" className="workspace__section-title">
              知识库
            </h2>
            {knowledgeBases === null ? (
              <p className="workspace__note">正在加载</p>
            ) : knowledgeBases.length === 0 ? (
              <p className="workspace__note">
                还没有知识库。管理员可以在下一步创建，成员上传资料时选择知识库。
              </p>
            ) : (
              <ul className="kb-list">
                {knowledgeBases.map((kb) => (
                  <li key={kb.id} className="kb-list__item">
                    <span>{kb.name}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
