import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  api,
  ApiError,
  type AnalysisCitationOut,
  type AnalysisDetailOut,
  type AnalysisFieldInput,
  type AnalysisFieldResultOut,
  type AnalysisSummaryOut,
  type AnalysisTaskOut,
  type DocumentItem,
} from "../lib/api";
import { Icon } from "./Icon";

type Props = {
  teamId: string;
  teamName: string;
  knowledgeBaseId: string;
  knowledgeBaseName: string;
  documents: DocumentItem[];
  onError: (message: string) => void;
};

type DraftField = AnalysisFieldInput & { key: string };

const VALUE_TYPES: Array<{
  value: AnalysisFieldInput["value_type"];
  label: string;
}> = [
  { value: "text", label: "文本" },
  { value: "number", label: "数字" },
  { value: "date", label: "日期" },
  { value: "text_list", label: "文本列表" },
];

const TASK_STATUS: Record<AnalysisTaskOut["status"], string> = {
  queued: "排队",
  running: "处理中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  invalidated: "来源已失效",
};

const DOCUMENT_STATUS: Record<AnalysisDetailOut["documents"][number]["status"], string> = {
  queued: "排队",
  running: "处理中",
  succeeded: "成功",
  failed: "处理失败",
};

const FIELD_STATUS: Record<AnalysisFieldResultOut["status"], string> = {
  extracted: "已提取",
  not_mentioned: "未提及",
  needs_review: "待核实",
  failed: "处理失败",
};

const STAGE_LABEL: Record<AnalysisDetailOut["task"]["stage"], string> = {
  queued: "等待后台处理",
  extracting: "正在逐段核对资料",
  comparing: "正在汇总对比",
  finished: "处理已结束",
};

const isActive = (task: AnalysisTaskOut) =>
  task.status === "queued" || task.status === "running";

const newDraftField = (): DraftField => ({
  key: crypto.randomUUID(),
  name: "",
  description: "",
  value_type: "text",
});

const errorMessage = (err: unknown, fallback: string) => {
  if (!(err instanceof ApiError)) return fallback;
  if (err.code === "ANALYSIS_LIMIT_EXCEEDED") {
    return `${err.message}。可等待已有任务结束后再提交。`;
  }
  if (err.code === "ANALYSIS_MODEL_UNAVAILABLE") {
    return "分析模型暂不可用，请稍后重新创建分析。";
  }
  if (err.code === "ANALYSIS_MODEL_OUTPUT_INVALID") {
    return "模型返回的结构未通过校验，本次分析已经停止。";
  }
  if (err.code === "ANALYSIS_SOURCE_INVALIDATED") {
    return "所选资料已删除或发布文本发生变化，本次结果已停止展示。";
  }
  return err.message;
};

const formatTime = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

const formatResultValue = (field: AnalysisFieldResultOut): string => {
  if (field.status === "not_mentioned") return "全文已处理，未找到该字段";
  if (field.status === "failed") return "该字段处理失败";
  if (field.value_list?.length) return field.value_list.join("、");
  if (field.value_number !== null && field.value_number !== undefined) {
    return field.raw_value || `${field.value_number}${field.unit ? ` ${field.unit}` : ""}`;
  }
  if (field.value_date) {
    if (field.raw_value) return field.raw_value;
    const precision =
      field.date_precision === "year"
        ? "年"
        : field.date_precision === "month"
          ? "月"
          : "日";
    return `${field.value_date}（精确到${precision}）`;
  }
  return field.value_text || field.raw_value || "原文未给出明确值";
};

function CitationList({
  citations,
  label,
}: {
  citations: AnalysisCitationOut[];
  label: string;
}) {
  if (citations.length === 0) return null;
  return (
    <details className="analysis-citation-group" open>
      <summary>
        <span>
          <Icon name="quote" size={15} />
          {label}
          <span className="analysis-citation-group__count">{citations.length}</span>
        </span>
        <Icon name="chevron" size={16} />
      </summary>
      <ol>
        {citations.map((citation, index) => (
          <li key={citation.id}>
            <div className="analysis-citation__head">
              <span>{index + 1}</span>
              <p>
                <strong>{citation.document_title}</strong>
                <small>{citation.locator_label}</small>
              </p>
            </div>
            <blockquote>{citation.excerpt}</blockquote>
          </li>
        ))}
      </ol>
    </details>
  );
}

function HistoryList({
  tasks,
  activeId,
  loading,
  nextCursor,
  onSelect,
  onMore,
}: {
  tasks: AnalysisTaskOut[];
  activeId: string | null;
  loading: boolean;
  nextCursor: string | null;
  onSelect: (task: AnalysisTaskOut) => void;
  onMore: () => void;
}) {
  return (
    <aside className="analysis-history" aria-labelledby="analysis-history-heading">
      <div className="analysis-history__head">
        <h3 id="analysis-history-heading">本人记录</h3>
        <span>{tasks.length}</span>
      </div>
      {loading && tasks.length === 0 ? (
        <p className="analysis-history__state" role="status">
          正在加载…
        </p>
      ) : tasks.length === 0 ? (
        <p className="analysis-history__state">
          提交后会保存在这里，刷新页面仍可继续核对。
        </p>
      ) : (
        <ul>
          {tasks.map((task) => (
            <li key={task.id}>
              <button
                type="button"
                className={task.id === activeId ? "is-active" : ""}
                aria-current={task.id === activeId ? "true" : undefined}
                onClick={() => onSelect(task)}
              >
                <span className={`analysis-status analysis-status--${task.status}`}>
                  {TASK_STATUS[task.status]}
                </span>
                <strong>{task.title}</strong>
                <small>
                  {task.document_count} 份资料 · {formatTime(task.created_at)}
                </small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {nextCursor && (
        <button
          className="btn btn--ghost analysis-history__more"
          type="button"
          onClick={onMore}
          disabled={loading}
        >
          加载更多
        </button>
      )}
    </aside>
  );
}

function TaskProgress({ detail }: { detail: AnalysisDetailOut }) {
  const document = detail.documents[0];
  const task = detail.task;
  return (
    <div className="analysis-progress" role="status" aria-live="polite">
      <div className="analysis-progress__mark">
        <span />
      </div>
      <div>
        <h3>{STAGE_LABEL[task.stage]}</h3>
        <p>
          已处理 {task.completed_document_count}/{task.document_count} 份资料
          {task.failed_document_count > 0
            ? `，失败 ${task.failed_document_count} 份`
            : ""}
        </p>
        {document && (
          <p>
            {document.document_title}：{DOCUMENT_STATUS[document.status]}
            {document.expected_chunk_count > 0 && document.status === "running"
              ? `，已核对 ${document.processed_chunk_count}/${document.expected_chunk_count} 个片段`
              : ""}
          </p>
        )}
      </div>
    </div>
  );
}

function ResultTable({ detail }: { detail: AnalysisDetailOut }) {
  const resultByDocument = new Map(
    detail.results.map((result) => [result.document_id, result]),
  );
  const fieldsById = new Map(detail.fields.map((field) => [field.id, field]));

  return (
    <>
      <div
        className="analysis-table-wrap"
        tabIndex={0}
        aria-label="提取结果表，可横向滚动"
      >
        <table className="analysis-table">
          <thead>
            <tr>
              <th scope="col">字段</th>
              {detail.documents.map((document) => (
                <th scope="col" key={document.document_id}>
                  <span>{document.document_title}</span>
                  <small
                    className={`analysis-status analysis-status--doc-${document.status}`}
                  >
                    {DOCUMENT_STATUS[document.status]}
                  </small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {detail.fields.map((field) => (
              <tr key={field.id}>
                <th scope="row">
                  <strong>{field.name}</strong>
                  <small>{field.description}</small>
                </th>
                {detail.documents.map((document) => {
                  const result = resultByDocument
                    .get(document.document_id)
                    ?.fields.find((item) => item.field_id === field.id);
                  return (
                    <td key={document.document_id}>
                      {result ? (
                        <>
                          <span
                            className={`analysis-status analysis-status--field-${result.status}`}
                          >
                            {FIELD_STATUS[result.status]}
                          </span>
                          <p>{formatResultValue(result)}</p>
                          {result.note && <small>{result.note}</small>}
                          {result.raw_value &&
                            result.raw_value !== formatResultValue(result) && (
                              <small>原文表达：{result.raw_value}</small>
                            )}
                        </>
                      ) : (
                        <span className="analysis-table__unavailable">
                          {document.status === "failed"
                            ? "未生成可用结果"
                            : "等待处理完成"}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail.results.some((result) =>
        result.fields.some((field) => (field.citations?.length ?? 0) > 0),
      ) && (
        <section className="analysis-evidence" aria-labelledby="analysis-evidence-heading">
          <div className="analysis-section-heading">
            <div>
              <h3 id="analysis-evidence-heading">原文依据</h3>
              <p>依据默认展开，可逐条折叠；位置与摘录均来自本次固定的资料快照。</p>
            </div>
          </div>
          <div className="analysis-evidence__groups">
            {detail.results.flatMap((result) =>
              result.fields.map((fieldResult) => {
                const field = fieldsById.get(fieldResult.field_id);
                const citations = fieldResult.citations ?? [];
                if (!field || citations.length === 0) return null;
                return (
                  <div key={`${result.document_id}-${fieldResult.field_id}`}>
                    <div className="analysis-evidence__title">
                      <strong>{field.name}</strong>
                      <span>{result.document_title}</span>
                    </div>
                    <CitationList
                      citations={citations}
                      label={`${field.name}的依据`}
                    />
                  </div>
                );
              }),
            )}
          </div>
        </section>
      )}
    </>
  );
}

const CONCLUSION_LABEL: Record<
  AnalysisDetailOut["conclusions"][number]["kind"],
  string
> = {
  common: "共同点",
  difference: "差异",
  needs_review: "待核实",
};

function ConclusionList({ detail }: { detail: AnalysisDetailOut }) {
  if (detail.conclusions.length === 0) return null;
  const fieldsById = new Map(detail.fields.map((field) => [field.id, field]));
  return (
    <section className="analysis-conclusions" aria-labelledby="analysis-conclusions-heading">
      <div className="analysis-section-heading">
        <div>
          <h3 id="analysis-conclusions-heading">对比结论</h3>
          <p>仅基于本次已完成提取的字段与依据，未比较的维度不作一致判断。</p>
        </div>
      </div>
      <div className="analysis-conclusion-list">
        {detail.conclusions.map((conclusion) => (
          <article
            className={`analysis-conclusion analysis-conclusion--${conclusion.kind}`}
            key={conclusion.id}
          >
            <div className="analysis-conclusion__head">
              <span className={`analysis-conclusion__kind is-${conclusion.kind}`}>
                {CONCLUSION_LABEL[conclusion.kind]}
              </span>
              <strong>{fieldsById.get(conclusion.field_id)?.name ?? "对比维度"}</strong>
            </div>
            <p>{conclusion.text}</p>
            <small>
              参与资料：
              {detail.documents
                .filter((document) => conclusion.document_ids.includes(document.document_id))
                .map((document) => document.document_title)
                .join("、")}
            </small>
            {conclusion.citations.length > 0 && (
              <CitationList
                citations={conclusion.citations}
                label={`${fieldsById.get(conclusion.field_id)?.name ?? "结论"}的依据`}
              />
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function SummaryState({ summary }: { summary: AnalysisSummaryOut | null }) {
  if (!summary || summary.status === "succeeded") return null;
  if (summary.status === "failed") {
    return (
      <p className="analysis-summary-error" role="alert">
        逐文档提取已完成，但对比汇总失败：{summary.error_detail || "请稍后重新运行"}。
      </p>
    );
  }
  return (
    <p className="analysis-progress analysis-progress--summary" role="status">
      {summary.status === "queued"
        ? "全部资料已提取，正在等待生成对比结论。"
        : "正在根据已校验的字段和依据生成对比结论。"}
    </p>
  );
}

function AnalysisDetailView({
  detail,
  canRerun,
  onRerun,
}: {
  detail: AnalysisDetailOut;
  canRerun: boolean;
  onRerun: () => void;
}) {
  const completed =
    detail.task.status === "completed" || detail.task.status === "partial";
  const sourceInvalid = !detail.source_valid || detail.task.status === "invalidated";
  const providers = new Set(
    [
      ...detail.documents.map((document) => document.provider),
      detail.summary?.provider,
    ].filter((provider): provider is string => Boolean(provider)),
  );
  return (
    <article className="analysis-detail" aria-labelledby="analysis-result-heading">
      <div className="analysis-detail__head">
        <div>
          <div className="analysis-detail__eyebrow">
            <span>{detail.task.kind === "extraction" ? "信息提取" : "文档对比"}</span>
            <span>{formatTime(detail.task.created_at)}</span>
          </div>
          <h2 id="analysis-result-heading">{detail.task.title}</h2>
        </div>
        <div className="analysis-detail__actions">
          <span className={`analysis-status analysis-status--${detail.task.status}`}>
            {TASK_STATUS[detail.task.status]}
          </span>
          {canRerun && !sourceInvalid && (
            <button
              className="btn btn--ghost analysis-rerun"
              type="button"
              onClick={onRerun}
            >
              重新运行
            </button>
          )}
        </div>
      </div>

      {sourceInvalid ? (
        <section className="analysis-invalid" role="status">
          <Icon name="shield" size={24} />
          <div>
            <h3>来源已失效，结果不再展示</h3>
            <p>{detail.invalid_reason || "所选资料已删除或发布文本发生变化。"}</p>
            <p>任务范围与字段定义仍保留。调整资料范围后可新建分析。</p>
          </div>
        </section>
      ) : isActive(detail.task) ? (
        <TaskProgress detail={detail} />
      ) : detail.task.status === "failed" ? (
        <section className="analysis-failure" role="alert">
          <h3>本次分析未完成</h3>
          {detail.documents.map((document) => (
            <div key={document.document_id}>
              <strong>{document.document_title}</strong>
              <p>{document.error_detail || "处理未完成，请稍后重新创建分析。"}</p>
              {document.attempts > 0 && <small>已尝试 {document.attempts} 次</small>}
            </div>
          ))}
        </section>
      ) : null}

      {!sourceInvalid && providers.size === 1 && providers.has("stub") && (
        <p className="analysis-stub-note" role="note">
          当前结果来自验证用固定规则替身，只用于检查流程和依据结构；不代表真实模型的提取或对比质量。
        </p>
      )}

      {!sourceInvalid && completed && (
        <>
          {detail.task.status === "partial" && (
            <p className="analysis-partial-note" role="status">
              部分资料未处理成功。成功资料的结果仍可核对，失败资料不展示半成品。
            </p>
          )}
          <ResultTable detail={detail} />
          {detail.task.kind === "comparison" && (
            <>
              <SummaryState summary={detail.summary ?? null} />
              <ConclusionList detail={detail} />
            </>
          )}
        </>
      )}
    </article>
  );
}

export function AnalysisPanel({
  teamId,
  teamName,
  knowledgeBaseId,
  knowledgeBaseName,
  documents,
  onError,
}: Props) {
  const [tasks, setTasks] = useState<AnalysisTaskOut[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AnalysisDetailOut | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailReload, setDetailReload] = useState(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [kind, setKind] = useState<AnalysisDetailOut["task"]["kind"]>("extraction");
  const [title, setTitle] = useState("");
  const [fields, setFields] = useState<DraftField[]>([newDraftField()]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // 重新运行会预填旧配置并记录来源任务；用户改动表单后视为新分析，不再挂来源。
  const [sourceTask, setSourceTask] = useState<{
    id: string;
    teamId: string;
    knowledgeBaseId: string;
  } | null>(null);
  const idempotencyKey = useRef(crypto.randomUUID());
  const historyRequest = useRef(0);
  const detailRequest = useRef(0);

  const readyDocuments = useMemo(
    () => documents.filter((document) => document.status === "ready"),
    [documents],
  );
  const availableIds = new Set(readyDocuments.map((document) => document.id));
  const activeSelectedIds = selectedIds.filter((id) => availableIds.has(id));
  const effectiveSelectedIds =
    activeSelectedIds.length > 0
      ? activeSelectedIds
      : readyDocuments[0]
        ? [readyDocuments[0].id]
        : [];

  const loadHistory = useCallback(
    async (
      append = false,
      cursor: string | null = null,
      scope: { teamId: string; knowledgeBaseId: string } = { teamId, knowledgeBaseId },
    ) => {
      const request = ++historyRequest.current;
      try {
        const response = await api.listAnalyses(
          scope.teamId,
          scope.knowledgeBaseId,
          append ? cursor ?? undefined : undefined,
        );
        if (request !== historyRequest.current) return;
        setTasks((current) => {
          if (!append) return response.items;
          const known = new Set(current.map((item) => item.id));
          return [...current, ...response.items.filter((item) => !known.has(item.id))];
        });
        setNextCursor(response.next_cursor ?? null);
        setSelectedId((current) => current ?? response.items[0]?.id ?? null);
      } catch (err) {
        if (request === historyRequest.current) {
          onError(errorMessage(err, "加载分析记录失败，请重试"));
        }
      } finally {
        if (request === historyRequest.current) setHistoryLoading(false);
      }
    },
    [knowledgeBaseId, onError, teamId],
  );

  useEffect(() => {
    const timer = setTimeout(() => void loadHistory(), 0);
    return () => {
      clearTimeout(timer);
      historyRequest.current += 1;
      detailRequest.current += 1;
    };
  }, [knowledgeBaseId, loadHistory, teamId]);

  useEffect(() => {
    if (!selectedId) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const request = ++detailRequest.current;
    const load = async () => {
      setDetailLoading(true);
      try {
        const response = await api.getAnalysis(teamId, knowledgeBaseId, selectedId);
        if (cancelled || request !== detailRequest.current) return;
        setDetail(response);
        setTasks((current) =>
          current.map((task) => (task.id === response.task.id ? response.task : task)),
        );
        if (isActive(response.task)) {
          timer = setTimeout(() => void load(), 1500);
        }
      } catch (err) {
        if (!cancelled && request === detailRequest.current) {
          setDetailError(errorMessage(err, "加载分析详情失败，请重试"));
        }
      } finally {
        if (!cancelled && request === detailRequest.current) setDetailLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [detailReload, knowledgeBaseId, selectedId, teamId]);

  function updateField(key: string, patch: Partial<AnalysisFieldInput>) {
    // 重新运行预填后，用户改动表单即视为新分析，不再挂来源任务。
    setSourceTask(null);
    setFields((current) =>
      current.map((field) => (field.key === key ? { ...field, ...patch } : field)),
    );
  }

  function selectTask(task: AnalysisTaskOut) {
    setSelectedId(task.id);
    setSubmitError(null);
  }

  function rerun() {
    if (!detail) return;
    setSourceTask({
      id: detail.task.id,
      teamId,
      knowledgeBaseId,
    });
    setKind(detail.task.kind);
    setTitle(detail.task.title);
    setFields(
      detail.fields.map((field) => ({
        key: crypto.randomUUID(),
        name: field.name,
        description: field.description,
        value_type: field.value_type,
      })),
    );
    setSelectedIds(detail.documents.map((document) => document.document_id));
    setSubmitError(null);
    idempotencyKey.current = crypto.randomUUID();
    document.getElementById("analysis-create-heading")?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const cleanFields = fields.map(({ key: _key, ...field }) => ({
      ...field,
      name: field.name.trim(),
      description: field.description.trim(),
    }));
    if (effectiveSelectedIds.length === 0) {
      setSubmitError("请选择至少一份处理完成的资料。");
      return;
    }
    if (kind === "comparison" && effectiveSelectedIds.length < 2) {
      setSubmitError("文档对比至少需要选择两份资料。");
      return;
    }
    if (
      cleanFields.length === 0 ||
      cleanFields.some((field) => !field.name || !field.description)
    ) {
      setSubmitError("请填写每个字段的名称和说明。");
      return;
    }
    const duplicate = cleanFields.some(
      (field, index) =>
        cleanFields.findIndex((other) => other.name === field.name) !== index,
    );
    if (duplicate) {
      setSubmitError("字段名称不能重复。");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    try {
      const task = await api.createAnalysis(
        teamId,
        knowledgeBaseId,
        {
          kind,
          document_ids: effectiveSelectedIds,
          fields: cleanFields,
          title: title.trim() || null,
          source_task_id:
            sourceTask?.teamId === teamId &&
            sourceTask.knowledgeBaseId === knowledgeBaseId
              ? sourceTask.id
              : null,
        },
        idempotencyKey.current,
      );
      idempotencyKey.current = crypto.randomUUID();
      setSourceTask(null);
      setFields([newDraftField()]);
      setTitle("");
      setSelectedIds([]);
      setTasks((current) => [
        task,
        ...current.filter((item) => item.id !== task.id),
      ]);
      setSelectedId(task.id);
    } catch (err) {
      setSubmitError(errorMessage(err, "提交分析失败，请检查资料和字段后重试。"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="analysis-panel" aria-labelledby="analysis-heading">
      <div className="panel__head analysis-panel__head">
        <div className="panel__heading">
          <Icon name="book" size={19} />
          <h2 id="analysis-heading">信息提取</h2>
        </div>
        <span className="analysis-panel__privacy">
          <Icon name="shield" size={14} />
          仅本人可见
        </span>
      </div>
      <div className="analysis-layout">
        <HistoryList
          tasks={tasks}
          activeId={selectedId}
          loading={historyLoading}
          nextCursor={nextCursor}
          onSelect={selectTask}
          onMore={() => void loadHistory(true, nextCursor)}
        />
        <div className="analysis-main">
          <section className="analysis-create" aria-labelledby="analysis-create-heading">
            <div className="analysis-section-heading">
              <div>
                <h3 id="analysis-create-heading">新建分析</h3>
                <p>
                  {teamName} / {knowledgeBaseName} · 结果仅创建者本人可见
                </p>
              </div>
            </div>
            {readyDocuments.length === 0 ? (
              <div className="analysis-empty">
                <Icon name="file" size={26} />
                <div>
                  <strong>还没有可用于分析的资料</strong>
                  <p>先在左侧上传资料，等待处理完成后即可提取字段。</p>
                </div>
              </div>
            ) : (
              <form onSubmit={submit} aria-busy={submitting}>
                <div className="analysis-kind-switch" aria-label="分析类型">
                  <button
                    type="button"
                    aria-pressed={kind === "extraction"}
                    onClick={() => {
                      setSourceTask(null);
                      setKind("extraction");
                    }}
                    disabled={submitting}
                  >
                    信息提取
                  </button>
                  <button
                    type="button"
                    aria-pressed={kind === "comparison"}
                    onClick={() => {
                      setSourceTask(null);
                      setKind("comparison");
                    }}
                    disabled={submitting}
                  >
                    文档对比
                  </button>
                </div>
                <fieldset className="analysis-document-picker">
                  <legend>选择资料</legend>
                  <p>
                    {kind === "comparison"
                      ? "选择至少两份资料，逐项比较用户指定的字段。"
                      : "可选择一份或多份资料；失败资料不会覆盖成功结果。"}
                  </p>
                  <div>
                    {readyDocuments.map((document) => (
                      <label key={document.id}>
                        <input
                          type="checkbox"
                          checked={effectiveSelectedIds.includes(document.id)}
                          onChange={(event) => {
                            setSourceTask(null);
                            setSelectedIds((current) => {
                              const chosen = new Set(
                                current.length > 0
                                  ? current.filter((id) => availableIds.has(id))
                                  : effectiveSelectedIds,
                              );
                              if (event.target.checked) chosen.add(document.id);
                              else chosen.delete(document.id);
                              return [...chosen];
                            });
                          }}
                          disabled={submitting}
                        />
                        <span>{document.filename}</span>
                      </label>
                    ))}
                  </div>
                  <small>
                    已选 {effectiveSelectedIds.length} / {readyDocuments.length} 份
                  </small>
                </fieldset>
                <div className="analysis-form-grid">
                  <label className="analysis-field">
                    <span>分析标题（可选）</span>
                    <input
                      value={title}
                      maxLength={120}
                      onChange={(event) => {
                        setSourceTask(null);
                        setTitle(event.target.value);
                      }}
                      placeholder={
                        kind === "comparison"
                          ? "例如：两份制度发布日期对比"
                          : "例如：差旅制度字段核对"
                      }
                      disabled={submitting}
                    />
                    <small>留空时自动生成</small>
                  </label>
                </div>
                <fieldset className="analysis-fields">
                  <legend>字段定义</legend>
                  {fields.map((field, index) => (
                    <div className="analysis-field-row" key={field.key}>
                      <label className="analysis-field">
                        <span>字段 {index + 1} 名称</span>
                        <input
                          value={field.name}
                          maxLength={80}
                          onChange={(event) =>
                            updateField(field.key, { name: event.target.value })
                          }
                          placeholder="例如：适用对象"
                          required
                          disabled={submitting}
                        />
                      </label>
                      <label className="analysis-field analysis-field--description">
                        <span>提取说明</span>
                        <input
                          value={field.description}
                          maxLength={500}
                          onChange={(event) =>
                            updateField(field.key, { description: event.target.value })
                          }
                          placeholder="说明要查找什么、如何理解"
                          required
                          disabled={submitting}
                        />
                      </label>
                      <label className="analysis-field analysis-field--type">
                        <span>类型</span>
                        <select
                          value={field.value_type}
                          onChange={(event) =>
                            updateField(field.key, {
                              value_type: event.target
                                .value as AnalysisFieldInput["value_type"],
                            })
                          }
                          disabled={submitting}
                        >
                          {VALUE_TYPES.map((type) => (
                            <option value={type.value} key={type.value}>
                              {type.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        className="icon-button analysis-field-row__remove"
                        type="button"
                        aria-label={`删除字段 ${index + 1}`}
                        onClick={() => {
                          setSourceTask(null);
                          setFields((current) =>
                            current.filter((item) => item.key !== field.key),
                          );
                        }}
                        disabled={submitting || fields.length === 1}
                      >
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  ))}
                  <button
                    className="btn btn--ghost analysis-add-field"
                    type="button"
                    onClick={() => {
                      setSourceTask(null);
                      setFields((current) => [...current, newDraftField()]);
                    }}
                    disabled={submitting || fields.length >= 20}
                  >
                    <Icon name="plus" size={16} />
                    添加字段
                  </button>
                </fieldset>
                {submitError && (
                  <p className="error-text analysis-submit-error" role="alert">
                    {submitError}
                  </p>
                )}
                <div className="analysis-submit">
                  <span>提交后后台继续处理，离开页面不会取消任务。</span>
                  <button className="btn" type="submit" disabled={submitting}>
                    {submitting
                      ? "提交中…"
                      : kind === "comparison"
                        ? "开始对比"
                        : "开始提取"}
                  </button>
                </div>
              </form>
            )}
          </section>

          {detailError ? (
            <div className="analysis-detail-message" role="alert">
              <p>{detailError}</p>
              <button
                className="btn btn--ghost"
                type="button"
                onClick={() => setDetailReload((revision) => revision + 1)}
              >
                重新加载
              </button>
            </div>
          ) : detailLoading && !detail ? (
            <p className="analysis-detail-message" role="status">
              正在加载分析记录…
            </p>
          ) : detail ? (
            <AnalysisDetailView
              detail={detail}
              canRerun={
                detail.task.status !== "queued" &&
                detail.task.status !== "running" &&
                detail.task.stage !== "comparing"
              }
              onRerun={rerun}
            />
          ) : (
            <div className="analysis-detail-message analysis-detail-message--blank">
              <Icon name="quote" size={24} />
              <p>提交后可在这里查看进度、信息表与原文依据。</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
