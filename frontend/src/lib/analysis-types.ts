// 由后端合同自动生成，请勿手工编辑。
//
// 生成命令：cd backend && .venv/bin/python -m app.typegen
// 一致性校验：cd backend && .venv/bin/python -m app.typegen --check
//
// 来源：backend/app/schemas.py 中 EXPORTED_MODELS 登记的 V2/V4 合同模型。

export type AnalysisFieldInput = {
  name: string;
  description: string;
  value_type: "text" | "number" | "date" | "text_list";
};

export type AnalysisCreateRequest = {
  kind: "extraction" | "comparison";
  document_ids: string[];
  fields: AnalysisFieldInput[];
  title?: string | null;
  source_task_id?: string | null;
};

export type AnalysisFieldOut = {
  id: string;
  ordinal: number;
  name: string;
  description: string;
  value_type: "text" | "number" | "date" | "text_list";
};

export type AnalysisTaskOut = {
  id: string;
  knowledge_base_id: string;
  kind: "extraction" | "comparison";
  title: string;
  status: "queued" | "running" | "completed" | "partial" | "failed" | "invalidated";
  stage: "queued" | "extracting" | "comparing" | "finished";
  document_count: number;
  completed_document_count: number;
  failed_document_count: number;
  source_task_id: string | null;
  error_code: "ANALYSIS_INVALID_INPUT" | "ANALYSIS_SOURCE_UNAVAILABLE" | "ANALYSIS_IDEMPOTENCY_CONFLICT" | "ANALYSIS_LIMIT_EXCEEDED" | "ANALYSIS_MODEL_UNAVAILABLE" | "ANALYSIS_MODEL_OUTPUT_INVALID" | "ANALYSIS_BUDGET_EXHAUSTED" | "ANALYSIS_SOURCE_INVALIDATED" | "ANALYSIS_PERMISSION_REVOKED" | "ANALYSIS_EXHAUSTED" | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
};

export type AnalysisTaskListOut = {
  items: AnalysisTaskOut[];
  next_cursor?: string | null;
};

export type AnalysisCitationOut = {
  id: string;
  document_id: string;
  document_title: string;
  locator_label: string;
  excerpt: string;
  table_number?: number | null;
  table_row?: number | null;
  table_headers?: string[] | null;
  table_cells?: string[] | null;
};

export type AnalysisFieldResultOut = {
  field_id: string;
  status: "extracted" | "not_mentioned" | "needs_review" | "failed";
  value_text?: string | null;
  value_number?: number | null;
  value_date?: string | null;
  value_list?: string[] | null;
  raw_value?: string | null;
  unit?: string | null;
  date_precision?: "year" | "month" | "day" | null;
  note?: string | null;
  citations?: AnalysisCitationOut[];
};

export type AnalysisDocumentOut = {
  document_id: string;
  document_title: string;
  ordinal: number;
  status: "queued" | "running" | "succeeded" | "failed";
  attempts: number;
  expected_chunk_count: number;
  processed_chunk_count: number;
  provider?: string | null;
  model?: string | null;
  error_code: "ANALYSIS_INVALID_INPUT" | "ANALYSIS_SOURCE_UNAVAILABLE" | "ANALYSIS_IDEMPOTENCY_CONFLICT" | "ANALYSIS_LIMIT_EXCEEDED" | "ANALYSIS_MODEL_UNAVAILABLE" | "ANALYSIS_MODEL_OUTPUT_INVALID" | "ANALYSIS_BUDGET_EXHAUSTED" | "ANALYSIS_SOURCE_INVALIDATED" | "ANALYSIS_PERMISSION_REVOKED" | "ANALYSIS_EXHAUSTED" | null;
  error_detail: string | null;
};

export type AnalysisDocumentResultOut = {
  document_id: string;
  document_title: string;
  ordinal: number;
  fields: AnalysisFieldResultOut[];
};

export type AnalysisSummaryOut = {
  status: "queued" | "running" | "succeeded" | "failed";
  attempts: number;
  provider?: string | null;
  model?: string | null;
  error_code?: "ANALYSIS_INVALID_INPUT" | "ANALYSIS_SOURCE_UNAVAILABLE" | "ANALYSIS_IDEMPOTENCY_CONFLICT" | "ANALYSIS_LIMIT_EXCEEDED" | "ANALYSIS_MODEL_UNAVAILABLE" | "ANALYSIS_MODEL_OUTPUT_INVALID" | "ANALYSIS_BUDGET_EXHAUSTED" | "ANALYSIS_SOURCE_INVALIDATED" | "ANALYSIS_PERMISSION_REVOKED" | "ANALYSIS_EXHAUSTED" | null;
  error_detail?: string | null;
};

export type AnalysisConclusionOut = {
  id: string;
  field_id: string;
  kind: "common" | "difference" | "needs_review";
  text: string;
  document_ids: string[];
  citations: AnalysisCitationOut[];
};

export type AnalysisDetailOut = {
  task: AnalysisTaskOut;
  fields: AnalysisFieldOut[];
  documents: AnalysisDocumentOut[];
  results: AnalysisDocumentResultOut[];
  conclusions: AnalysisConclusionOut[];
  summary?: AnalysisSummaryOut | null;
  source_valid: boolean;
  invalid_reason?: string | null;
};

export type EvidenceSnapshotInput = {
  document_id?: string | null;
  document_title: string;
  locator_label?: string;
  excerpt: string;
};

export type EvidenceSnapshotOut = {
  document_id?: string | null;
  document_title: string;
  locator_label?: string;
  excerpt: string;
};

export type NoteCreateRequest = {
  title: string;
  content: string;
  source_type?: "manual" | "document" | "conversation" | "report";
  source_id?: string | null;
  citations?: EvidenceSnapshotInput[];
};

export type NoteUpdateRequest = {
  title: string;
  content: string;
  citations?: EvidenceSnapshotInput[];
};

export type NoteOut = {
  id: string;
  knowledge_base_id: string;
  title: string;
  content: string;
  source_type: "manual" | "document" | "conversation" | "report";
  source_id: string | null;
  citations: EvidenceSnapshotOut[];
  created_at: string;
  updated_at: string;
};

export type NoteListOut = {
  items: NoteOut[];
};

export type MissingItemOut = {
  kind: "missing" | "review" | "source";
  text: string;
};

export type ReportGapOut = {
  items: MissingItemOut[];
  coverage_note: string;
};

export type ReportCitationOut = {
  document_id?: string | null;
  document_title: string;
  locator_label?: string;
  excerpt: string;
  id: string;
  ordinal: number;
};

export type ReportSectionOut = {
  id: string;
  ordinal: number;
  heading: string;
  body: string;
  citations: ReportCitationOut[];
};

export type ReportSectionSuggestionRequest = {
  instruction: string;
};

export type ReportSectionSuggestionOut = {
  original: string;
  suggestion: string;
  rationale: string;
  provider: string;
};

export type ReportSectionUpdate = {
  id?: string | null;
  heading: string;
  body: string;
};

export type ReportSourceOut = {
  id: string;
  source_type: "analysis" | "document" | "note";
  source_id: string;
  title_snapshot: string;
  available: boolean;
};

export type ReportOut = {
  id: string;
  knowledge_base_id: string;
  source_analysis_id: string | null;
  title: string;
  purpose: string;
  focus: string;
  length: "short" | "medium" | "long";
  status: "draft" | "reviewed";
  needs_review: boolean;
  review_note: string | null;
  source_review_required: boolean;
  source_review_note: string | null;
  gaps: MissingItemOut[];
  sections: ReportSectionOut[];
  sources: ReportSourceOut[];
  created_at: string;
  updated_at: string;
};

export type ReportSummaryOut = {
  id: string;
  title: string;
  status: "draft" | "reviewed";
  needs_review: boolean;
  source_review_required: boolean;
  source_analysis_id: string | null;
  section_count: number;
  created_at: string;
  updated_at: string;
};

export type ReportListOut = {
  items: ReportSummaryOut[];
};

export type ReportFromAnalysisRequest = {
  title?: string | null;
  purpose?: string;
  focus?: string;
  length?: "short" | "medium" | "long";
};

export type ReportFromSourcesRequest = {
  title: string;
  purpose?: string;
  focus?: string;
  length?: "short" | "medium" | "long";
  document_ids?: string[];
  note_ids?: string[];
};

export type ReportUpdateRequest = {
  title: string;
  purpose?: string;
  focus?: string;
  length: "short" | "medium" | "long";
  status: "draft" | "reviewed";
  source_review_acknowledged?: boolean;
  gaps?: MissingItemOut[];
  sections: ReportSectionUpdate[];
};

export type SavedRequirementCreate = {
  kind: "analysis_fields" | "report";
  name: string;
  payload: Record<string, unknown>;
};

export type SavedRequirementOut = {
  id: string;
  kind: "analysis_fields" | "report";
  name: string;
  payload: Record<string, unknown>;
  updated_at: string;
};

export type SavedRequirementListOut = {
  items: SavedRequirementOut[];
};

export type AnalysisSuggestionRequest = {
  requirement: string;
  document_ids?: string[];
};

export type AnalysisSuggestionOut = {
  fields: AnalysisFieldInput[];
  rationale: string;
};

export type DocumentGuideOut = {
  document_id: string;
  document_title: string;
  summary: string;
  section_labels: string[];
  key_concepts: string[];
  suggested_questions: string[];
  coverage_note: string;
};

export type DocumentSearchHitOut = {
  document_id: string;
  document_title: string;
  locator_label: string;
  excerpt: string;
  before: string;
  after: string;
};

export type DocumentSearchOut = {
  query: string;
  hits: DocumentSearchHitOut[];
};

export type DocumentOut = {
  id: string;
  knowledge_base_id: string;
  title: string;
  filename: string;
  folder_id?: string | null;
  sort_order?: number;
  size_bytes: number;
  status: string;
  failure_reason: string | null;
  created_at: string;
  source_url?: string | null;
  replaces_id?: string | null;
};

export type TextDocumentCreate = {
  knowledge_base_id: string;
  title: string;
  content: string;
  source_url?: string | null;
  folder_id?: string | null;
};

export type WebDocumentCreate = {
  knowledge_base_id: string;
  url: string;
  title?: string | null;
  folder_id?: string | null;
};

export type DocumentImpactOut = {
  document_id: string;
  document_title: string;
  note_count: number;
  analysis_count: number;
  report_count: number;
  stale_report_ids: string[];
};

export type DocumentReplaceOut = {
  document: DocumentOut;
  affected: DocumentImpactOut;
};

export type AutomationCreateRequest = {
  kind: "extraction" | "comparison";
  document_ids: string[];
  fields: AnalysisFieldInput[];
  analysis_title?: string | null;
  report_title?: string;
  report_purpose?: string;
  report_focus?: string;
  report_length?: "short" | "medium" | "long";
};

export type AutomationStepOut = {
  id: string;
  ordinal: number;
  kind: string;
  status: string;
  target_id: string | null;
  error_code: string | null;
  detail: string | null;
  created_at: string;
  finished_at: string | null;
};

export type AutomationOut = {
  id: string;
  knowledge_base_id: string;
  title: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled" | "invalidated";
  stage: "queued" | "analysis" | "report" | "canceling" | "finished";
  analysis_kind: "extraction" | "comparison";
  document_ids: string[];
  document_count: number;
  fields: AnalysisFieldInput[];
  analysis_title: string;
  report_title: string;
  report_purpose: string;
  report_focus: string;
  report_length: "short" | "medium" | "long";
  analysis_id: string | null;
  report_id: string | null;
  attempts: number;
  max_attempts: number;
  error_code: "AUTOMATION_INVALID_INPUT" | "AUTOMATION_SOURCE_UNAVAILABLE" | "AUTOMATION_IDEMPOTENCY_CONFLICT" | "AUTOMATION_LIMIT_EXCEEDED" | "AUTOMATION_ANALYSIS_PARTIAL" | "AUTOMATION_ANALYSIS_FAILED" | "AUTOMATION_ANALYSIS_INVALIDATED" | "AUTOMATION_REPORT_FAILED" | "AUTOMATION_PERMISSION_REVOKED" | "AUTOMATION_EXHAUSTED" | null;
  error_detail: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type AutomationDetailOut = {
  id: string;
  knowledge_base_id: string;
  title: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled" | "invalidated";
  stage: "queued" | "analysis" | "report" | "canceling" | "finished";
  analysis_kind: "extraction" | "comparison";
  document_ids: string[];
  document_count: number;
  fields: AnalysisFieldInput[];
  analysis_title: string;
  report_title: string;
  report_purpose: string;
  report_focus: string;
  report_length: "short" | "medium" | "long";
  analysis_id: string | null;
  report_id: string | null;
  attempts: number;
  max_attempts: number;
  error_code: "AUTOMATION_INVALID_INPUT" | "AUTOMATION_SOURCE_UNAVAILABLE" | "AUTOMATION_IDEMPOTENCY_CONFLICT" | "AUTOMATION_LIMIT_EXCEEDED" | "AUTOMATION_ANALYSIS_PARTIAL" | "AUTOMATION_ANALYSIS_FAILED" | "AUTOMATION_ANALYSIS_INVALIDATED" | "AUTOMATION_REPORT_FAILED" | "AUTOMATION_PERMISSION_REVOKED" | "AUTOMATION_EXHAUSTED" | null;
  error_detail: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  steps: AutomationStepOut[];
};

export type AutomationListOut = {
  items: AutomationOut[];
};
