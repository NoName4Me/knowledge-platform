import type {
  AnalysisCreateRequest,
  AnalysisDetailOut,
  AnalysisSuggestionOut,
  AnalysisTaskListOut,
  AnalysisTaskOut,
  DocumentGuideOut,
  DocumentImpactOut,
  DocumentReplaceOut,
  DocumentSearchOut,
  NoteCreateRequest,
  NoteListOut,
  NoteOut,
  NoteUpdateRequest,
  ReportFromAnalysisRequest,
  ReportFromSourcesRequest,
  ReportGapOut,
  ReportListOut,
  ReportOut,
  ReportSectionSuggestionOut,
  ReportUpdateRequest,
  SavedRequirementCreate,
  SavedRequirementOut,
  SavedRequirementListOut,
  TextDocumentCreate,
  WebDocumentCreate,
} from "./analysis-types";

export type {
  AnalysisCitationOut,
  AnalysisConclusionOut,
  AnalysisCreateRequest,
  AnalysisDetailOut,
  AnalysisDocumentOut,
  AnalysisDocumentResultOut,
  AnalysisFieldInput,
  AnalysisFieldOut,
  AnalysisFieldResultOut,
  AnalysisSummaryOut,
  AnalysisTaskListOut,
  AnalysisTaskOut,
  AnalysisSuggestionOut,
  DocumentOut,
  DocumentGuideOut,
  DocumentImpactOut,
  DocumentReplaceOut,
  DocumentSearchOut,
  NoteCreateRequest,
  NoteListOut,
  NoteOut,
  NoteUpdateRequest,
  ReportFromAnalysisRequest,
  ReportFromSourcesRequest,
  ReportGapOut,
  ReportListOut,
  ReportOut,
  ReportSectionOut,
  ReportSectionSuggestionOut,
  ReportSummaryOut,
  ReportUpdateRequest,
  SavedRequirementCreate,
  SavedRequirementOut,
  SavedRequirementListOut,
  TextDocumentCreate,
  WebDocumentCreate,
} from "./analysis-types";

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

export type DocumentFolder = {
  id: string;
  knowledge_base_id: string;
  parent_id: string | null;
  name: string;
  sort_order?: number;
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
  folder_id: string | null;
  id: string;
  knowledge_base_id: string;
  title: string;
  filename: string;
  sort_order?: number;
  size_bytes: number;
  status: DocumentStatus;
  failure_reason: string | null;
  created_at: string;
  source_url?: string | null;
  replaces_id?: string | null;
};

export type Citation = {
  number?: number | null;
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
  scope_document_ids: string[];
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
  code: string | null;
  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
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
    const detail = body?.detail;
    const envelope = body?.error;
    if (
      envelope &&
      typeof envelope === "object" &&
      typeof envelope.message === "string"
    ) {
      throw new ApiError(
        response.status,
        envelope.message,
        typeof envelope.code === "string" ? envelope.code : null,
      );
    }
    if (typeof detail === "string") {
      throw new ApiError(response.status, detail);
    }
    if (
      detail &&
      typeof detail === "object" &&
      typeof detail.message === "string"
    ) {
      throw new ApiError(
        response.status,
        detail.message,
        typeof detail.code === "string" ? detail.code : null,
      );
    }
    throw new ApiError(response.status, "请求失败");
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
  renameKnowledgeBase: (teamId: string, kbId: string, name: string) =>
    writeRequest<KnowledgeBase>(`/api/teams/${teamId}/knowledge-bases/${kbId}`, jsonInit("PATCH", { name })),
  deleteKnowledgeBase: (teamId: string, kbId: string) =>
    writeRequest<void>(`/api/teams/${teamId}/knowledge-bases/${kbId}`, { method: "DELETE" }),
  listFolders: (teamId: string, kbId: string) =>
    request<DocumentFolder[]>(`/api/teams/${teamId}/knowledge-bases/${kbId}/folders`),
  createFolder: (teamId: string, kbId: string, name: string, parentId: string | null = null) =>
    writeRequest<DocumentFolder>(`/api/teams/${teamId}/knowledge-bases/${kbId}/folders`, jsonInit("POST", { name, parent_id: parentId })),
  renameFolder: (teamId: string, kbId: string, folderId: string, name: string) =>
    writeRequest<DocumentFolder>(`/api/teams/${teamId}/knowledge-bases/${kbId}/folders/${folderId}`, jsonInit("PATCH", { name })),
  reorderTree: (teamId: string, kbId: string, parentId: string | null, items: Array<{ kind: "folder" | "document"; id: string }>, moving?: { kind: "folder" | "document"; id: string }) =>
    writeRequest<DocumentFolder[]>(`/api/teams/${teamId}/knowledge-bases/${kbId}/folders/reorder`, jsonInit("POST", { parent_id: parentId, items, moving: moving ?? null })),
  moveFolder: (teamId: string, kbId: string, folderId: string, parentId: string | null) =>
    writeRequest<DocumentFolder>(`/api/teams/${teamId}/knowledge-bases/${kbId}/folders/${folderId}/parent`, jsonInit("PATCH", { parent_id: parentId })),
  deleteFolder: (teamId: string, kbId: string, folderId: string) =>
    writeRequest<void>(`/api/teams/${teamId}/knowledge-bases/${kbId}/folders/${folderId}`, { method: "DELETE" }),
  updateDocument: (teamId: string, documentId: string, patch: { filename?: string; folder_id?: string | null }) =>
    writeRequest<DocumentItem>(`/api/teams/${teamId}/documents/${documentId}`, jsonInit("PATCH", patch)),
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
  uploadDocument: (teamId: string, knowledgeBaseId: string, file: File, folderId?: string | null) => {
    const form = new FormData();
    form.append("knowledge_base_id", knowledgeBaseId);
    form.append("file", file);
    if (folderId) form.append("folder_id", folderId);
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
  createTextDocument: (teamId: string, payload: TextDocumentCreate) =>
    writeRequest<DocumentItem>(
      `/api/teams/${teamId}/documents/text`,
      jsonInit(FORM_METHOD, payload),
    ),
  createWebDocument: (teamId: string, payload: WebDocumentCreate) =>
    writeRequest<DocumentItem>(
      `/api/teams/${teamId}/documents/web`,
      jsonInit(FORM_METHOD, payload),
    ),
  getDocumentImpact: (teamId: string, documentId: string) =>
    request<DocumentImpactOut>(`/api/teams/${teamId}/documents/${documentId}/impact`),
  replaceDocument: (teamId: string, documentId: string, knowledgeBaseId: string, file: File) => {
    const form = new FormData();
    form.append("knowledge_base_id", knowledgeBaseId);
    form.append("file", file);
    return writeRequest<DocumentReplaceOut>(
      `/api/teams/${teamId}/documents/${documentId}/replace`,
      {
        method: FORM_METHOD,
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: form,
      },
    );
  },
  getDocumentGuide: (teamId: string, kbId: string, documentId: string) =>
    request<DocumentGuideOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/documents/${documentId}/guide`,
    ),
  searchDocuments: (teamId: string, kbId: string, query: string, documentId?: string) => {
    const params = new URLSearchParams({ q: query });
    if (documentId) params.set("document_id", documentId);
    return request<DocumentSearchOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/document-search?${params}`,
    );
  },

  listNotes: (teamId: string, kbId: string) =>
    request<NoteListOut>(`/api/teams/${teamId}/knowledge-bases/${kbId}/notes`),
  createNote: (teamId: string, kbId: string, payload: NoteCreateRequest) =>
    writeRequest<NoteOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/notes`,
      jsonInit(FORM_METHOD, payload),
    ),
  updateNote: (teamId: string, kbId: string, noteId: string, payload: NoteUpdateRequest) =>
    writeRequest<NoteOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/notes/${noteId}`,
      jsonInit("PUT", payload),
    ),
  deleteNote: (teamId: string, kbId: string, noteId: string) =>
    writeRequest<void>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/notes/${noteId}`,
      { method: "DELETE" },
    ),

  listReports: (teamId: string, kbId: string) =>
    request<ReportListOut>(`/api/teams/${teamId}/knowledge-bases/${kbId}/reports`),
  getReport: (teamId: string, kbId: string, reportId: string) =>
    request<ReportOut>(`/api/teams/${teamId}/knowledge-bases/${kbId}/reports/${reportId}`),
  createReportFromAnalysis: (
    teamId: string,
    kbId: string,
    analysisId: string,
    payload: ReportFromAnalysisRequest,
  ) =>
    writeRequest<ReportOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/from-analysis/${analysisId}`,
      jsonInit(FORM_METHOD, payload),
    ),
  createReportFromSources: (
    teamId: string,
    kbId: string,
    payload: ReportFromSourcesRequest,
  ) =>
    writeRequest<ReportOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/from-sources`,
      jsonInit(FORM_METHOD, payload),
    ),
  updateReport: (teamId: string, kbId: string, reportId: string, payload: ReportUpdateRequest) =>
    writeRequest<ReportOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/${reportId}`,
      jsonInit("PUT", payload),
    ),
  deleteReport: (teamId: string, kbId: string, reportId: string) =>
    writeRequest<void>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/${reportId}`,
      { method: "DELETE" },
    ),
  getReportGaps: (teamId: string, kbId: string, reportId: string) =>
    request<ReportGapOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/${reportId}/gaps`,
    ),
  suggestReportSection: (
    teamId: string,
    kbId: string,
    reportId: string,
    sectionId: string,
    instruction: string,
  ) =>
    writeRequest<ReportSectionSuggestionOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/${reportId}/sections/${sectionId}/suggest`,
      jsonInit(FORM_METHOD, { instruction }),
    ),

  suggestAnalysisFields: (
    teamId: string,
    kbId: string,
    requirement: string,
    documentIds: string[],
  ) =>
    writeRequest<AnalysisSuggestionOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/analysis-suggestions`,
      jsonInit(FORM_METHOD, { requirement, document_ids: documentIds }),
    ),
  listRequirements: (teamId: string, kbId: string, kind?: "analysis_fields" | "report") => {
    const query = kind ? `?kind=${kind}` : "";
    return request<SavedRequirementListOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/requirements${query}`,
    );
  },
  createRequirement: (teamId: string, kbId: string, payload: SavedRequirementCreate) =>
    writeRequest<SavedRequirementOut>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/requirements`,
      jsonInit(FORM_METHOD, payload),
    ),
  deleteRequirement: (teamId: string, kbId: string, requirementId: string) =>
    writeRequest<void>(
      `/api/teams/${teamId}/knowledge-bases/${kbId}/requirements/${requirementId}`,
      { method: "DELETE" },
    ),
  reportExportUrl: (teamId: string, kbId: string, reportId: string, format: "docx" | "csv" | "xlsx" | "pdf") =>
    `/api/teams/${teamId}/knowledge-bases/${kbId}/reports/${reportId}/export.${format}`,

  listAnalyses: (teamId: string, knowledgeBaseId: string, cursor?: string) => {
    const query = new URLSearchParams();
    if (cursor) query.set("cursor", cursor);
    const suffix = query.toString();
    return request<AnalysisTaskListOut>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/analyses` +
        (suffix ? `?${suffix}` : ""),
    );
  },
  createAnalysis: (
    teamId: string,
    knowledgeBaseId: string,
    payload: AnalysisCreateRequest,
    idempotencyKey: string,
  ) =>
    writeRequest<AnalysisTaskOut>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/analyses`,
      {
        ...jsonInit(FORM_METHOD, payload),
        // 重复点击复用同一提交标识，避免创建重复任务。
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
      },
    ),
  getAnalysis: (teamId: string, knowledgeBaseId: string, analysisId: string) =>
    request<AnalysisDetailOut>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/analyses/${analysisId}`,
    ),

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
    documentIds?: string[],
  ) =>
    writeRequest<Turn>(
      `/api/teams/${teamId}/knowledge-bases/${knowledgeBaseId}/conversations/` +
        `${conversationId}/turns`,
      jsonInit(FORM_METHOD, {
        client_request_id: clientRequestId,
        question,
        ...(documentIds ? { document_ids: documentIds } : {}),
      }),
    ),
  streamConversationTurn: async (
    teamId: string,
    knowledgeBaseId: string,
    conversationId: string,
    clientRequestId: string,
    question: string,
    documentIds: string[] | undefined,
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
          ...(documentIds ? { document_ids: documentIds } : {}),
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
