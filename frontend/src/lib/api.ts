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

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export type DocumentItem = {
  id: string;
  knowledge_base_id: string;
  title: string;
  filename: string;
  size_bytes: number;
  status: DocumentStatus;
  failure_reason: string | null;
  created_at: string;
};

export type Citation = {
  document_id: string;
  document_title: string;
  locator_label: string;
  excerpt: string;
};

export type AskResponse = {
  answer: string;
  grounded: boolean;
  citations: Citation[];
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

function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

export const api = {
  login: (email: string, password: string) =>
    request<MeResponse>("/api/auth/login", jsonInit("POST", { email, password })),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<MeResponse>("/api/auth/me"),

  listKnowledgeBases: (teamId: string) =>
    request<KnowledgeBase[]>(`/api/teams/${teamId}/knowledge-bases`),
  createKnowledgeBase: (teamId: string, name: string) =>
    request<KnowledgeBase>(`/api/teams/${teamId}/knowledge-bases`, jsonInit("POST", { name })),

  listDocuments: (teamId: string, knowledgeBaseId: string) =>
    request<DocumentItem[]>(
      `/api/teams/${teamId}/documents?knowledge_base_id=${knowledgeBaseId}`,
    ),
  uploadDocument: (teamId: string, knowledgeBaseId: string, file: File) => {
    const form = new FormData();
    form.append("knowledge_base_id", knowledgeBaseId);
    form.append("file", file);
    return request<DocumentItem>(`/api/teams/${teamId}/documents`, {
      method: "POST",
      body: form,
    });
  },
  deleteDocument: (teamId: string, documentId: string) =>
    request<void>(`/api/teams/${teamId}/documents/${documentId}`, { method: "DELETE" }),

  ask: (teamId: string, knowledgeBaseId: string, question: string) =>
    request<AskResponse>(
      `/api/teams/${teamId}/ask`,
      jsonInit("POST", { knowledge_base_id: knowledgeBaseId, question }),
    ),
};
