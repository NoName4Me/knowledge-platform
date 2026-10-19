export type TeamSummary = {
  id: string;
  name: string;
  role: "admin" | "member";
};

export type MeResponse = {
  user: { id: string; email: string; display_name: string };
  teams: TeamSummary[];
};

export type KnowledgeBase = {
  id: string;
  name: string;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : "请求失败";
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<MeResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<MeResponse>("/api/auth/me"),
  listKnowledgeBases: (teamId: string) =>
    request<KnowledgeBase[]>(`/api/teams/${teamId}/knowledge-bases`),
  createKnowledgeBase: (teamId: string, name: string) =>
    request<KnowledgeBase>(`/api/teams/${teamId}/knowledge-bases`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
};
