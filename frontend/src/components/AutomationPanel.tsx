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
  type AnalysisFieldInput,
  type AnalysisSuggestionOut,
  type AutomationDetailOut,
  type AutomationOut,
  type DocumentItem,
  type SavedRequirementOut,
} from "../lib/api";
import { Icon } from "./Icon";
import { SelectMenu } from "./Popover";

type Props = {
  teamId: string;
  teamName: string;
  knowledgeBaseId: string;
  knowledgeBaseName: string;
  documents: DocumentItem[];
  onError: (message: string) => void;
  onOpenAnalysis: (analysisId: string) => void;
  onOpenReport: (reportId: string) => void;
};

type DraftField = AnalysisFieldInput & { key: string };
type AutomationStatus = AutomationOut["status"];

const VALUE_TYPES: Array<{
  value: AnalysisFieldInput["value_type"];
  label: string;
}> = [
  { value: "text", label: "文本" },
  { value: "number", label: "数字" },
  { value: "date", label: "日期" },
  { value: "text_list", label: "文本列表" },
];

const STATUS_LABEL: Record<AutomationStatus, string> = {
  queued: "排队",
  running: "执行中",
  completed: "报告待核对",
  failed: "失败",
  canceled: "已取消",
  invalidated: "来源失效",
};

const STAGE_LABEL: Record<AutomationOut["stage"], string> = {
  queued: "等待后台安排",
  analysis: "正在整理所选资料",
  report: "正在进行报告成稿",
  canceling: "正在取消",
  finished: "流程已结束",
};

const STEP_LABEL: Record<string, string> = {
  start: "确认要求",
  analysis: "信息分析",
  report: "报告草稿",
  cancel: "取消执行",
  continue: "继续执行",
};

const STEP_STATUS_LABEL: Record<string, string> = {
  started: "进行中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
};

const isActive = (item: Pick<AutomationOut, "status">) =>
  item.status === "queued" || item.status === "running";

const canContinue = (item: AutomationOut) =>
  item.status === "failed" &&
  ![
    "AUTOMATION_ANALYSIS_PARTIAL",
    "AUTOMATION_ANALYSIS_INVALIDATED",
    "AUTOMATION_PERMISSION_REVOKED",
  ].includes(item.error_code ?? "");

const newDraftField = (): DraftField => ({
  key: crypto.randomUUID(),
  name: "",
  description: "",
  value_type: "text",
});

const formatTime = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

function errorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.code === "AUTOMATION_LIMIT_EXCEEDED") {
    return `${error.message}。请等待已有执行结束或取消后再试。`;
  }
  if (error.code === "AUTOMATION_SOURCE_UNAVAILABLE") {
    return "所选资料已不可用，请重新选择处理完成的资料。";
  }
  if (error.code === "AUTOMATION_IDEMPOTENCY_CONFLICT") {
    return "本次提交标识已用于另一组要求，请刷新后重新提交。";
  }
  return error.message;
}

function History({
  items,
  activeId,
  loading,
  onSelect,
}: {
  items: AutomationOut[];
  activeId: string | null;
  loading: boolean;
  onSelect: (item: AutomationOut) => void;
}) {
  return (
    <aside className="analysis-history automation-history" aria-labelledby="automation-history-heading">
      <div className="analysis-history__head">
        <h3 id="automation-history-heading">本人执行</h3>
        <span>{items.length}</span>
      </div>
      {loading && items.length === 0 ? (
        <p className="analysis-history__state" role="status">正在加载…</p>
      ) : items.length === 0 ? (
        <p className="analysis-history__state">
          确认资料和报告要求后，执行记录会保存在这里。
        </p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={item.id === activeId ? "is-active" : ""}
                aria-current={item.id === activeId ? "true" : undefined}
                onClick={() => onSelect(item)}
              >
                <span className={`automation-status automation-status--${item.status}`}>
                  {STATUS_LABEL[item.status]}
                </span>
                <strong>{item.title}</strong>
                <small>{item.document_count} 份资料 · {formatTime(item.updated_at)}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function StepList({ detail }: { detail: AutomationDetailOut }) {
  return (
    <ol className="automation-steps" aria-label="执行步骤">
      {detail.steps.map((step) => (
        <li key={step.id} className={`automation-step automation-step--${step.status}`}>
          <span className="automation-step__mark" aria-hidden="true">
            {step.status === "succeeded" ? <Icon name="check" size={14} /> : null}
          </span>
          <div>
            <div className="automation-step__title">
              <strong>{STEP_LABEL[step.kind] ?? step.kind}</strong>
              <span>{STEP_STATUS_LABEL[step.status] ?? step.status}</span>
              <small>{formatTime(step.created_at)}</small>
            </div>
            {step.detail && <p>{step.detail}</p>}
            {step.error_code && <small>错误码：{step.error_code}</small>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function AutomationPanel({
  teamId,
  teamName,
  knowledgeBaseId,
  knowledgeBaseName,
  documents,
  onError,
  onOpenAnalysis,
  onOpenReport,
}: Props) {
  const [items, setItems] = useState<AutomationOut[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AutomationDetailOut | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailReload, setDetailReload] = useState(0);
  const [kind, setKind] = useState<AutomationOut["analysis_kind"]>("extraction");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [fields, setFields] = useState<DraftField[]>([newDraftField()]);
  const [analysisTitle, setAnalysisTitle] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const [reportPurpose, setReportPurpose] = useState("");
  const [reportFocus, setReportFocus] = useState("");
  const [reportLength, setReportLength] = useState<AutomationOut["report_length"]>("medium");
  const [requirementText, setRequirementText] = useState("");
  const [fieldSuggestion, setFieldSuggestion] = useState<AnalysisSuggestionOut | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [savedFields, setSavedFields] = useState<SavedRequirementOut[]>([]);
  const [savedReports, setSavedReports] = useState<SavedRequirementOut[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const idempotencyKey = useRef(crypto.randomUUID());
  const listRequest = useRef(0);
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

  const loadHistory = useCallback(async () => {
    const request = ++listRequest.current;
    try {
      const response = await api.listAutomations(teamId, knowledgeBaseId);
      if (request !== listRequest.current) return;
      setItems(response.items);
      setSelectedId((current) => current ?? response.items[0]?.id ?? null);
    } catch (error) {
      if (request === listRequest.current) {
        onError(errorMessage(error, "加载自动成稿记录失败，请重试"));
      }
    } finally {
      if (request === listRequest.current) setHistoryLoading(false);
    }
  }, [knowledgeBaseId, onError, teamId]);

  useEffect(() => {
    const timer = setTimeout(() => void loadHistory(), 0);
    return () => {
      clearTimeout(timer);
      listRequest.current += 1;
      detailRequest.current += 1;
    };
  }, [loadHistory]);

  useEffect(() => {
    if (!items.some(isActive)) return;
    const timer = setTimeout(() => void loadHistory(), 2500);
    return () => clearTimeout(timer);
  }, [items, loadHistory]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.listRequirements(teamId, knowledgeBaseId, "analysis_fields"),
      api.listRequirements(teamId, knowledgeBaseId, "report"),
    ])
      .then(([analysisResponse, reportResponse]) => {
        if (cancelled) return;
        setSavedFields(analysisResponse.items);
        setSavedReports(reportResponse.items);
      })
      .catch((error) => {
        if (!cancelled) onError(errorMessage(error, "加载常用要求失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [knowledgeBaseId, onError, teamId]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const request = ++detailRequest.current;
    const load = async () => {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await api.getAutomation(teamId, knowledgeBaseId, selectedId);
        if (cancelled || request !== detailRequest.current) return;
        setDetail(response);
        setItems((current) =>
          current.map((item) => item.id === response.id ? response : item),
        );
        if (isActive(response)) {
          timer = setTimeout(() => void load(), 1500);
        }
      } catch (error) {
        if (!cancelled && request === detailRequest.current) {
          setDetailError(errorMessage(error, "加载执行详情失败，请重试"));
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
    setFields((current) =>
      current.map((field) => field.key === key ? { ...field, ...patch } : field),
    );
  }

  function selectAutomation(item: AutomationOut) {
    setSelectedId(item.id);
    setDetailError(null);
  }

  async function suggestFields() {
    if (!requirementText.trim() || suggesting) return;
    setSuggesting(true);
    try {
      setFieldSuggestion(
        await api.suggestAnalysisFields(
          teamId,
          knowledgeBaseId,
          requirementText.trim(),
          effectiveSelectedIds,
        ),
      );
    } catch (error) {
      onError(errorMessage(error, "生成字段建议失败，可继续手工填写"));
    } finally {
      setSuggesting(false);
    }
  }

  function applyFieldSuggestion() {
    if (!fieldSuggestion) return;
    setFields(fieldSuggestion.fields.map((field) => ({
      ...field,
      key: crypto.randomUUID(),
    })));
    setFieldSuggestion(null);
  }

  function applySavedFields(preset: SavedRequirementOut) {
    const rawFields = preset.payload.fields;
    if (!Array.isArray(rawFields)) {
      onError("这项常用字段的数据格式无效");
      return;
    }
    const next = rawFields.flatMap((item): DraftField[] => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      if (
        typeof record.name !== "string" ||
        typeof record.description !== "string" ||
        !VALUE_TYPES.some((type) => type.value === record.value_type)
      ) return [];
      return [{
        key: crypto.randomUUID(),
        name: record.name,
        description: record.description,
        value_type: record.value_type as AnalysisFieldInput["value_type"],
      }];
    });
    if (next.length === 0) {
      onError("这项常用字段没有可用内容");
      return;
    }
    setFields(next);
  }

  function applySavedReport(preset: SavedRequirementOut) {
    const payload = preset.payload;
    setReportPurpose(typeof payload.purpose === "string" ? payload.purpose : "");
    setReportFocus(typeof payload.focus === "string" ? payload.focus : "");
    if (payload.length === "short" || payload.length === "medium" || payload.length === "long") {
      setReportLength(payload.length);
    }
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
    if (cleanFields.length === 0 || cleanFields.some((field) => !field.name || !field.description)) {
      setSubmitError("请填写每个字段的名称和提取说明。");
      return;
    }
    const duplicate = cleanFields.some(
      (field, index) => cleanFields.findIndex((other) => other.name === field.name) !== index,
    );
    if (duplicate) {
      setSubmitError("字段名称不能重复。");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await api.createAutomation(
        teamId,
        knowledgeBaseId,
        {
          kind,
          document_ids: effectiveSelectedIds,
          fields: cleanFields,
          analysis_title: analysisTitle.trim() || null,
          report_title: reportTitle.trim(),
          report_purpose: reportPurpose.trim(),
          report_focus: reportFocus.trim(),
          report_length: reportLength,
        },
        idempotencyKey.current,
      );
      idempotencyKey.current = crypto.randomUUID();
      setItems((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setSelectedId(created.id);
      setKind("extraction");
      setSelectedIds([]);
      setFields([newDraftField()]);
      setAnalysisTitle("");
      setReportTitle("");
      setReportPurpose("");
      setReportFocus("");
      setReportLength("medium");
      setRequirementText("");
      setFieldSuggestion(null);
    } catch (error) {
      setSubmitError(errorMessage(error, "启动自动成稿失败，请检查要求后重试。"));
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelSelected() {
    if (!detail || actionBusy || !isActive(detail)) return;
    if (!window.confirm("取消后不再创建或发布后续成果；已完成的分析会保留。确定取消？")) return;
    setActionBusy(true);
    try {
      const updated = await api.cancelAutomation(teamId, knowledgeBaseId, detail.id);
      setItems((current) => current.map((item) => item.id === updated.id ? updated : item));
      setDetail((current) => current ? { ...current, ...updated } : current);
    } catch (error) {
      onError(errorMessage(error, "取消执行失败"));
    } finally {
      setActionBusy(false);
    }
  }

  async function continueSelected() {
    if (!detail || actionBusy || !canContinue(detail)) return;
    setActionBusy(true);
    try {
      const updated = await api.continueAutomation(teamId, knowledgeBaseId, detail.id);
      setItems((current) => current.map((item) => item.id === updated.id ? updated : item));
      setDetail((current) => current ? { ...current, ...updated } : current);
    } catch (error) {
      onError(errorMessage(error, "继续执行失败，请确认来源和账号权限仍有效"));
    } finally {
      setActionBusy(false);
    }
  }

  async function deleteSelected() {
    if (!detail || actionBusy) return;
    if (!window.confirm(
      `删除自动成稿记录“${detail.title}”？已生成的分析和报告会保留；正在执行的流程会先停止。`,
    )) return;
    setActionBusy(true);
    try {
      await api.deleteAutomation(teamId, knowledgeBaseId, detail.id);
      setDetail(null);
      setSelectedId(null);
      setItems((current) => current.filter((item) => item.id !== detail.id));
      await loadHistory();
    } catch (error) {
      onError(errorMessage(error, "删除自动成稿记录失败"));
    } finally {
      setActionBusy(false);
    }
  }

  const requirementPlaceholder =
    kind === "comparison"
      ? "例如：比较三份合同的适用条件、主要差异和缺失信息，并生成带依据的报告"
      : "例如：提取三份制度中的适用范围、生效日期和责任主体，并生成带依据的报告";

  return (
    <section className="analysis-panel automation-panel" aria-labelledby="automation-heading">
      <div className="panel__head analysis-panel__head">
        <div className="panel__heading">
          <Icon name="send" size={19} />
          <h2 id="automation-heading">自动成稿</h2>
        </div>
      </div>
      <div className="analysis-layout">
        <History
          items={items}
          activeId={selectedId}
          loading={historyLoading}
          onSelect={selectAutomation}
        />
        <div className="analysis-main" role="region" aria-label="自动成稿配置与进度" tabIndex={0}>
          <section className="analysis-create automation-create" aria-labelledby="automation-create-heading">
            <div className="analysis-section-heading">
              <div>
                <h3 id="automation-create-heading">确认这次要生成什么</h3>
                <p>{teamName} / {knowledgeBaseName}。启动后会固定资料、字段和报告要求。</p>
              </div>
              <span className="automation-private"><Icon name="shield" size={14} />仅本人可见</span>
            </div>
            {readyDocuments.length === 0 ? (
              <div className="analysis-empty">
                <Icon name="file" size={26} />
                <div>
                  <strong>还没有可用于成稿的资料</strong>
                  <p>先在左侧上传资料，等待处理完成后即可开始。</p>
                </div>
              </div>
            ) : (
              <form onSubmit={submit} aria-busy={submitting}>
                <div className="analysis-kind-switch" aria-label="分析方式">
                  <button
                    type="button"
                    aria-pressed={kind === "extraction"}
                    onClick={() => setKind("extraction")}
                    disabled={submitting}
                  >
                    信息提取
                  </button>
                  <button
                    type="button"
                    aria-pressed={kind === "comparison"}
                    onClick={() => setKind("comparison")}
                    disabled={submitting}
                  >
                    文档对比
                  </button>
                </div>
                <fieldset className="analysis-document-picker">
                  <legend>1. 选择资料</legend>
                  <p>
                    {kind === "comparison"
                      ? "选择至少两份资料，逐项比较后形成报告。"
                      : "可选择一份或多份资料提取字段。"}
                  </p>
                  <div>
                    {readyDocuments.map((document) => (
                      <label key={document.id}>
                        <input
                          type="checkbox"
                          checked={effectiveSelectedIds.includes(document.id)}
                          onChange={(event) => {
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
                  <small>已选 {effectiveSelectedIds.length} / {readyDocuments.length} 份</small>
                </fieldset>

                <section className="analysis-field-assistant" aria-labelledby="automation-assistant-heading">
                  <div>
                    <h4 id="automation-assistant-heading">按报告目的建议字段</h4>
                    <p>建议只用于准备字段，确认后才会替换下方内容。</p>
                  </div>
                  <div className="analysis-field-assistant__input">
                    <textarea
                      value={requirementText}
                      onChange={(event) => setRequirementText(event.target.value)}
                      rows={3}
                      maxLength={2000}
                      placeholder={requirementPlaceholder}
                      disabled={submitting || suggesting}
                    />
                    <button
                      type="button"
                      className="btn btn--ghost"
                      disabled={submitting || suggesting || requirementText.trim().length < 2}
                      onClick={() => void suggestFields()}
                    >
                      {suggesting ? "生成中…" : "生成字段建议"}
                    </button>
                  </div>
                  {fieldSuggestion && (
                    <div className="analysis-field-suggestion" role="status">
                      <strong>建议字段</strong>
                      <ul>
                        {fieldSuggestion.fields.map((field) => (
                          <li key={field.name}>
                            <span>{field.name}</span>
                            <small>{field.description}</small>
                          </li>
                        ))}
                      </ul>
                      <p>{fieldSuggestion.rationale}</p>
                      <div>
                        <button type="button" className="btn btn--ghost" onClick={() => setFieldSuggestion(null)}>
                          不采用
                        </button>
                        <button type="button" className="btn" onClick={applyFieldSuggestion}>
                          填入并继续调整
                        </button>
                      </div>
                    </div>
                  )}
                </section>

                {(savedFields.length > 0 || savedReports.length > 0) && (
                  <div className="automation-presets">
                    {savedFields.length > 0 && (
                      <div>
                        <strong>常用字段</strong>
                        <div className="automation-presets__chips">
                          {savedFields.map((preset) => (
                            <button
                              type="button"
                              key={preset.id}
                              onClick={() => applySavedFields(preset)}
                              disabled={submitting}
                            >
                              {preset.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {savedReports.length > 0 && (
                      <div>
                        <strong>常用报告要求</strong>
                        <div className="automation-presets__chips">
                          {savedReports.map((preset) => (
                            <button
                              type="button"
                              key={preset.id}
                              onClick={() => applySavedReport(preset)}
                              disabled={submitting}
                            >
                              {preset.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                <fieldset className="analysis-fields">
                  <legend>2. 确认提取字段</legend>
                  {fields.map((field, index) => (
                    <div className="analysis-field-row" key={field.key}>
                      <label className="analysis-field">
                        <span>字段 {index + 1} 名称</span>
                        <input
                          value={field.name}
                          maxLength={80}
                          onChange={(event) => updateField(field.key, { name: event.target.value })}
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
                          onChange={(event) => updateField(field.key, { description: event.target.value })}
                          placeholder="说明要查找什么、如何理解"
                          required
                          disabled={submitting}
                        />
                      </label>
                      <div className="analysis-field analysis-field--type">
                        <span>类型</span>
                        <SelectMenu
                          className="analysis-field-select"
                          label={`字段 ${index + 1} 类型`}
                          value={field.value_type}
                          onChange={(value) => updateField(field.key, {
                            value_type: value as AnalysisFieldInput["value_type"],
                          })}
                          disabled={submitting}
                          options={VALUE_TYPES.map((type) => ({
                            id: type.value,
                            label: type.label,
                          }))}
                        />
                      </div>
                      <button
                        className="icon-button analysis-field-row__remove"
                        type="button"
                        aria-label={`删除字段 ${index + 1}`}
                        onClick={() => setFields((current) =>
                          current.filter((item) => item.key !== field.key))}
                        disabled={submitting || fields.length === 1}
                      >
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  ))}
                  <button
                    className="btn btn--ghost analysis-add-field"
                    type="button"
                    onClick={() => setFields((current) => [...current, newDraftField()])}
                    disabled={submitting || fields.length >= 20}
                  >
                    <Icon name="plus" size={16} />
                    添加字段
                  </button>
                </fieldset>

                <fieldset className="analysis-fields automation-report-requirements">
                  <legend>3. 确认报告要求</legend>
                  <div className="analysis-form-grid">
                    <label className="analysis-field">
                      <span>分析标题（可选）</span>
                      <input
                        value={analysisTitle}
                        maxLength={120}
                        onChange={(event) => setAnalysisTitle(event.target.value)}
                        placeholder="留空时按分析方式生成"
                        disabled={submitting}
                      />
                    </label>
                    <label className="analysis-field">
                      <span>报告名称（可选）</span>
                      <input
                        value={reportTitle}
                        maxLength={200}
                        onChange={(event) => setReportTitle(event.target.value)}
                        placeholder="留空时使用分析标题"
                        disabled={submitting}
                      />
                    </label>
                  </div>
                  <label className="analysis-field automation-text-field">
                    <span>报告用途</span>
                    <textarea
                      value={reportPurpose}
                      maxLength={2000}
                      rows={2}
                      onChange={(event) => setReportPurpose(event.target.value)}
                      placeholder="例如：供团队评审比较使用"
                      disabled={submitting}
                    />
                  </label>
                  <label className="analysis-field automation-text-field">
                    <span>报告重点</span>
                    <textarea
                      value={reportFocus}
                      maxLength={2000}
                      rows={3}
                      onChange={(event) => setReportFocus(event.target.value)}
                      placeholder="例如：适用范围、主要差异、缺失信息和待核实条件"
                      disabled={submitting}
                    />
                  </label>
                  <label className="analysis-field automation-length-field">
                    <span>篇幅</span>
                    <select
                      value={reportLength}
                      onChange={(event) =>
                        setReportLength(event.target.value as AutomationOut["report_length"])}
                      disabled={submitting}
                    >
                      <option value="short">简短</option>
                      <option value="medium">适中</option>
                      <option value="long">详细</option>
                    </select>
                  </label>
                </fieldset>

                {submitError && <p className="error-text analysis-submit-error" role="alert">{submitError}</p>}
                <div className="analysis-submit">
                  <span>启动后关闭页面也会继续；成功时只生成报告草稿，仍需人工核对。</span>
                  <button className="btn" type="submit" disabled={submitting}>
                    {submitting ? "正在启动…" : "确认并开始自动成稿"}
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
                onClick={() => setDetailReload((value) => value + 1)}
              >
                重新加载
              </button>
            </div>
          ) : detailLoading && !detail ? (
            <p className="analysis-detail-message" role="status">正在加载执行记录…</p>
          ) : detail ? (
            <article className="analysis-detail automation-detail" aria-labelledby="automation-detail-heading">
              <div className="analysis-detail__head">
                <div>
                  <div className="analysis-detail__eyebrow">
                    <span className={`automation-status automation-status--${detail.status}`}>
                      {STATUS_LABEL[detail.status]}
                    </span>
                    <span>{detail.analysis_kind === "comparison" ? "文档对比" : "信息提取"}</span>
                    <span>{detail.document_count} 份资料</span>
                  </div>
                  <h2 id="automation-detail-heading">{detail.title}</h2>
                </div>
                <div className="analysis-detail__actions">
                  {detail.analysis_id && (
                    <button
                      className="btn btn--ghost analysis-rerun"
                      type="button"
                      onClick={() => onOpenAnalysis(detail.analysis_id!)}
                    >
                      查看分析
                    </button>
                  )}
                  {detail.report_id && (
                    <button
                      className="btn analysis-rerun"
                      type="button"
                      onClick={() => onOpenReport(detail.report_id!)}
                    >
                      核对报告
                    </button>
                  )}
                  {isActive(detail) && (
                    <button
                      className="btn btn--ghost analysis-rerun"
                      type="button"
                      onClick={() => void cancelSelected()}
                      disabled={actionBusy}
                    >
                      取消执行
                    </button>
                  )}
                  {canContinue(detail) && (
                    <button
                      className="btn analysis-rerun"
                      type="button"
                      onClick={() => void continueSelected()}
                      disabled={actionBusy}
                    >
                      继续执行
                    </button>
                  )}
                  <button
                    className="btn btn--ghost analysis-delete"
                    type="button"
                    onClick={() => void deleteSelected()}
                    disabled={actionBusy}
                  >
                    <Icon name="trash" size={15} />
                    {actionBusy && isActive(detail) ? "正在停止并删除…" : "删除记录"}
                  </button>
                </div>
              </div>

              {detail.status === "completed" ? (
                <div className="automation-result-note">
                  <Icon name="check" size={18} />
                  <div>
                    <strong>报告草稿已生成</strong>
                    <p>分析与报告仅本人可见。请进入报告核对事实、条件和依据后再保存或导出。</p>
                  </div>
                </div>
              ) : detail.status === "failed" || detail.status === "invalidated" ? (
                <div className="analysis-failure" role="alert">
                  <h3>{detail.status === "invalidated" ? "来源已失效，执行已停止" : "执行未完成"}</h3>
                  <p>{detail.error_detail || "后台执行遇到问题，请查看步骤记录。"}</p>
                  {detail.error_code && <small>错误码：{detail.error_code}</small>}
                </div>
              ) : detail.status === "canceled" ? (
                <div className="automation-stopped-note">
                  <strong>执行已取消</strong>
                  <p>不再创建或发布后续成果，已生成的分析和报告仍可查看。</p>
                </div>
              ) : (
                <div className="analysis-progress" role="status" aria-live="polite">
                  <div className="analysis-progress__mark"><span /></div>
                  <div>
                    <h3>{STAGE_LABEL[detail.stage]}</h3>
                    <p>后台会自动衔接分析与报告，可以离开此页面。</p>
                  </div>
                </div>
              )}

              <section className="automation-plan" aria-labelledby="automation-plan-heading">
                <h3 id="automation-plan-heading">已确认要求</h3>
                <dl>
                  <div>
                    <dt>提取字段</dt>
                    <dd>{detail.fields.map((field) => field.name).join("、")}</dd>
                  </div>
                  <div>
                    <dt>报告重点</dt>
                    <dd>{detail.report_focus || "按字段结果组织报告"}</dd>
                  </div>
                  <div>
                    <dt>报告用途</dt>
                    <dd>{detail.report_purpose || "未额外指定"}</dd>
                  </div>
                  <div>
                    <dt>报告篇幅</dt>
                    <dd>{detail.report_length === "short" ? "简短" : detail.report_length === "long" ? "详细" : "适中"}</dd>
                  </div>
                </dl>
              </section>

              <section className="automation-timeline" aria-labelledby="automation-timeline-heading">
                <div className="automation-timeline__head">
                  <h3 id="automation-timeline-heading">执行步骤</h3>
                  <span>{detail.attempts}/{detail.max_attempts} 次尝试</span>
                </div>
                <StepList detail={detail} />
              </section>
            </article>
          ) : (
            <div className="analysis-detail-message analysis-detail-message--blank">
              <Icon name="send" size={24} />
              <p>确认要求并启动后，可在这里查看真实阶段、错误和成果入口。</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
