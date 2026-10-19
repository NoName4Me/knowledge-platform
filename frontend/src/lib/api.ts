export type TeamSummary = {
  id: string;
  name: string;
  role: "admin" | "member";
};

export type MeResponse = {
  user: { id: string; email: string; display_name: string };
  teams: TeamSummary[];
  csrf_token: string;
};

export type CsrfResponse = {
  csrf_token: string;
  expires_at: string;
};

export type KnowledgeBase = {
  id: string;
  name: string;
};

export type TeamMember = {
  user_id: string;
  email: string;
  display_name: string;
  role: "admin" | "member";
};

export type TeamInvitation = {
  id: string;
  email: string;
  role: "admin" | "member";
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
};

export type InvitationPreview = {
  status: "ok" | "used" | "expired" | "not_found";
  team_name: string | null;
  email_masked: string | null;
};

export type DocumentStatus = "uploading" | "pending" | "processing" | "ready" | "failed" | "deleted";

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

export type Conversation = {
  id: string;
  knowledge_base_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type TurnStatus = "answering" | "completed" | "insufficient" | "failed" | "interrupted";

export type Turn = {
  id: string;
  seq: number;
  client_request_id: string;
  question: string;
  status: TurnStatus;
  answer: string | null;
  answer_draft: string | null;
  error_code: string | null;
  started_at: string;
  finished_at: string | null;
  citations: Citation[];
};

export type TurnStreamEvent = {
  type: "turn" | "delta" | "error";
  turn?: Turn;
  text?: string;
  error_code?: string;
  message?: string;
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

// 写请求统一附带 CSRF 令牌；未登录时先取预登录令牌。
let csrfToken: string | null = null;
let csrfRequest: Promise<string> | null = null;

export function setCsrfToken(token: string): void {
  csrfToken = token;
  csrfRequest = null;
}

async function ensureCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  if (!csrfRequest) {
    csrfRequest = request<CsrfResponse>("/api/auth/csrf")
      .then((data) => {
        csrfToken = data.csrf_token;
        return data.csrf_token;
      })
      .finally(() => {
        csrfRequest = null;
      });
  }
  return csrfRequest;
}

async function writeRequest<T>(path: string, init: RequestInit): Promise<T> {
  const token = await ensureCsrfToken();
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", token);
  try {
    return await request<T>(path, { ...init, headers });
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      // 会话已轮换或过期：清掉缓存令牌后由下一次调用重新获取。
      csrfToken = null;
    }
    throw err;
  }
}

function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

const FORM_METHOD = "POST";

export const api = {
  login: async (email: string, password: string) => {
    const data = await writeRequest<MeResponse>(
      "/api/auth/login",
      jsonInit(FORM_METHOD, { email, password }),
    );
    setCsrfToken(data.csrf_token);
    return data;
  },
  logout: async () => {
    await writeRequest<void>("/api/auth/logout", { method: FORM_METHOD });
    setCsrfToken("");
  },
  me: async () => {
    const data = await request<MeResponse>("/api/auth/me");
    setCsrfToken(data.csrf_token);
    return data;
  },

  listKnowledgeBases: (teamId: string) =>
    request<KnowledgeBase[]>(`/api/teams/${teamId}/knowledge-bases`),
  createKnowledgeBase: (teamId: string, name: string) =>
    writeRequest<KnowledgeBase>(
      `/api/teams/${teamId}/knowledge-bases`,
      jsonInit(FORM_METHOD, { name }),
    ),
  listMembers: (teamId: string) => request<TeamMember[]>(`/api/teams/${teamId}/members`),
  removeMember: (teamId: string, userId: string) =>
    writeRequest<void>(`/api/teams/${teamId}/members/${userId}`, { method: "DELETE" }),
  listInvitations: (teamId: string) =>
    request<TeamInvitation[]>(`/api/teams/${teamId}/invitations`),
  createInvitation: (teamId: string, email: string, role: "admin" | "member") =>
    writeRequest<{ invitation: TeamInvitation; token: string }>(
      `/api/teams/${teamId}/invitations`,
      jsonInit(FORM_METHOD, { email, role }),
    ),
  revokeInvitation: (teamId: string, invitationId: string) =>
    writeRequest<void>(`/api/teams/${teamId}/invitations/${invitationId}`, {
      method: "DELETE",
    }),
  previewInvitation: (token: string) =>
    writeRequest<InvitationPreview>(
      "/api/invitations/preview",
      jsonInit(FORM_METHOD, { token }),
    ),
  registerWithInvitation: (token: string, email: string, displayName: string, password: string) =>
    writeRequest<MeResponse>(
      "/api/invitations/register",
      jsonInit(FORM_METHOD, { token, email, display_name: displayName, password }),
    ).then((data) => {
      setCsrfToken(data.csrf_token);
      return data;
    }),
  acceptInvitation: (token: string) =>
    writeRequest<MeResponse>("/api/invitations/accept", jsonInit(FORM_METHOD, { token })),

  listDocuments: (teamId: string, knowledgeBaseId: string) =>
    request<DocumentItem[]>(
      `/api/teams/${teamId}/documents?knowledge_base_id=${knowledgeBaseId}`,
    ),
  uploadDocument: (teamId: string, knowledgeBaseId: string, file: File) => {
    const form = new FormData();
    form.append("knowledge_base_id", knowledgeBaseId);
    form.append("file", file);
    return writeRequest<DocumentItem>(`/api/teams/${teamId}/documents`, {
      method: FORM_METHOD,
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: form,
    });
  },
  deleteDocument: (teamId: string, documentId: string) =>
    writeRequest<void>(`/api/teams/${teamId}/documents/${documentId}`, { method: "DELETE" }),
  retryDocument: (teamId: string, documentId: string) =>
    writeRequest<DocumentItem>(`/api/teams/${teamId}/documents/${documentId}/retry`, {
      method: FORM_METHOD,
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }),

  ask: (teamId: string, knowledgeBaseId: string, question: string) =>
    writeRequest<AskResponse>(
      `/api/teams/${teamId}/ask`,
      jsonInit(FORM_METHOD, { knowledge_base_id: knowledgeBaseId, question }),
    ),

  listConversations: (teamId: string, knowledgeBaseId: string) =>
    request<Conversation[]>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/conversations`,
    ),
  createConversation: (teamId: string, knowledgeBaseId: string, title?: string) =>
    writeRequest<Conversation>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/conversations`,
      jsonInit(FORM_METHOD, { title: title ?? null }),
    ),
  listTurns: (teamId: string, knowledgeBaseId: string, conversationId: string) =>
    request<Turn[]>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/conversations/` +
        `${conversationId}/turns`,
    ),
  askConversation: (
    teamId: string,
    knowledgeBaseId: string,
    conversationId: string,
    clientRequestId: string,
    question: string,
  ) =>
    writeRequest<Turn>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/conversations/` +
        `${conversationId}/turns`,
      jsonInit(FORM_METHOD, {
        client_request_id: clientRequestId,
        question,
      }),
    ),
  streamConversationTurn: async (
    teamId: string,
    knowledgeBaseId: string,
    conversationId: string,
    clientRequestId: string,
    question: string,
    onEvent: (event: TurnStreamEvent) => void,
    signal?: AbortSignal,
  ) => {
    const token = await ensureCsrfToken();
    const response = await fetch(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/conversations/` +
        `${conversationId}/turns/stream`,
      {
        method: FORM_METHOD,
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": token,
        },
        body: JSON.stringify({
          client_request_id: clientRequestId,
          question,
        }),
        signal,
      },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = body && typeof body.detail === "string" ? body.detail : "提问失败";
      throw new ApiError(response.status, detail);
    }
    if (!response.body) throw new ApiError(502, "浏览器不支持流式回答");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");
        if (!data || data === "{}") continue;
        onEvent(JSON.parse(data) as TurnStreamEvent);
      }
      if (done) break;
    }
  },
};
