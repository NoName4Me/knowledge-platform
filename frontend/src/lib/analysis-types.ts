// 由后端合同自动生成，请勿手工编辑。
//
// 生成命令：cd backend && .venv/bin/python -m app.typegen
// 一致性校验：cd backend && .venv/bin/python -m app.typegen --check
//
// 来源：backend/app/schemas.py 中 EXPORTED_MODELS 登记的 V2 合同模型。

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
