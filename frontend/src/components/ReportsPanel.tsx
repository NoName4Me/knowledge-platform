import { useEffect, useMemo, useState } from "react";
import {
  api,
  type DocumentItem,
  type NoteOut,
  type ReportOut,
  type ReportSectionOut,
  type ReportSectionSuggestionOut,
  type ReportSummaryOut,
  type SavedRequirementOut,
} from "../lib/api";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { documentLabel } from "../lib/document-label";

type Props = {
  teamId: string;
  knowledgeBaseId: string;
  documents: DocumentItem[];
  initialReportId?: string | null;
  onError: (message: string) => void;
  onPersistenceChange: (dirty: boolean) => void;
};

type EditableSection = ReportSectionOut & { key: string; dirty?: boolean };

const formatTime = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
    .format(new Date(value));

export function ReportsPanel({
  teamId,
  knowledgeBaseId,
  documents,
  initialReportId,
  onError,
  onPersistenceChange,
}: Props) {
  const [reports, setReports] = useState<ReportSummaryOut[]>([]);
  const [query, setQuery] = useState("");
  const [notes, setNotes] = useState<NoteOut[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(initialReportId ?? null);
  const [detail, setDetail] = useState<ReportOut | null>(null);
  const [sections, setSections] = useState<EditableSection[]>([]);
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [focus, setFocus] = useState("");
  const [length, setLength] = useState<ReportOut["length"]>("medium");
  const [status, setStatus] = useState<ReportOut["status"]>("draft");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftPurpose, setDraftPurpose] = useState("");
  const [draftFocus, setDraftFocus] = useState("");
  const [draftLength, setDraftLength] = useState<ReportOut["length"]>("medium");
  const [sourceDocuments, setSourceDocuments] = useState<string[]>([]);
  const [sourceNotes, setSourceNotes] = useState<string[]>([]);
  const [suggestFor, setSuggestFor] = useState<EditableSection | null>(null);
  const [instruction, setInstruction] = useState("");
  const [suggestion, setSuggestion] = useState<ReportSectionSuggestionOut | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [gaps, setGaps] = useState<ReportOut["gaps"]>([]);
  const [acknowledgeSourceReview, setAcknowledgeSourceReview] = useState(false);
  const [reportRequirements, setReportRequirements] = useState<SavedRequirementOut[]>([]);
  const [saveRequirementOpen, setSaveRequirementOpen] = useState(false);
  const [requirementName, setRequirementName] = useState("");

  const dirty = Boolean(detail) && (
    title !== detail?.title ||
    purpose !== detail?.purpose ||
    focus !== detail?.focus ||
    length !== detail?.length ||
    status !== detail?.status ||
    acknowledgeSourceReview ||
    JSON.stringify(gaps) !== JSON.stringify(detail?.gaps) ||
    sections.some((section) => section.dirty)
  );

  useEffect(() => {
    onPersistenceChange(dirty);
    return () => onPersistenceChange(false);
  }, [dirty, onPersistenceChange]);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  async function loadList(preselect?: string | null) {
    setLoading(true);
    try {
      const [reportResponse, noteResponse] = await Promise.all([
        api.listReports(teamId, knowledgeBaseId),
        api.listNotes(teamId, knowledgeBaseId),
      ]);
      setReports(reportResponse.items);
      setNotes(noteResponse.items);
      setSelectedId((current) => preselect ?? current ?? reportResponse.items[0]?.id ?? null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "加载报告失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadList(initialReportId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId, knowledgeBaseId, initialReportId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api.getReport(teamId, knowledgeBaseId, selectedId)
      .then((report) => {
        if (cancelled) return;
        setDetail(report);
        setTitle(report.title);
        setPurpose(report.purpose);
        setFocus(report.focus);
        setLength(report.length);
        setStatus(report.status);
        setGaps(report.gaps);
        setSections(report.sections.map((section) => ({ ...section, key: section.id })));
      })
      .catch((err) => {
        if (!cancelled) onError(err instanceof Error ? err.message : "加载报告失败");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [teamId, knowledgeBaseId, selectedId, onError]);

  useEffect(() => {
    let cancelled = false;
    api.listRequirements(teamId, knowledgeBaseId, "report")
      .then((response) => {
        if (!cancelled) setReportRequirements(response.items);
      })
      .catch((err) => {
        if (!cancelled) onError(err instanceof Error ? err.message : "加载常用报告要求失败");
      });
    return () => {
      cancelled = true;
    };
  }, [knowledgeBaseId, onError, teamId]);

  const readyDocuments = useMemo(
    () => documents.filter((document) => document.status === "ready"),
    [documents],
  );
  const filteredReports = useMemo(() => {
    const term = query.trim().toLocaleLowerCase("zh-CN");
    return term
      ? reports.filter((report) => report.title.toLocaleLowerCase("zh-CN").includes(term))
      : reports;
  }, [query, reports]);

  function updateSection(key: string, patch: Partial<EditableSection>) {
    setSections((items) => items.map((item) => item.key === key ? { ...item, ...patch, dirty: true } : item));
  }

  function moveSection(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= sections.length) return;
    setSections((items) => {
      const next = [...items];
      [next[index], next[target]] = [next[target], next[index]];
      return next.map((section) => ({ ...section, dirty: true }));
    });
  }

  function addSection() {
    setSections((items) => [
      ...items,
      {
        key: crypto.randomUUID(),
        id: "",
        ordinal: items.length,
        heading: "新章节",
        body: "请填写正文。",
        citations: [],
        dirty: true,
      },
    ]);
  }

  function removeSection(key: string) {
    if (sections.length <= 1) return;
    setSections((items) => items.filter((section) => section.key !== key));
  }

  async function saveDetail() {
    if (!detail || busy || !title.trim() || sections.some((section) => !section.heading.trim() || !section.body.trim())) return;
    setBusy(true);
    try {
      const saved = await api.updateReport(teamId, knowledgeBaseId, detail.id, {
        title: title.trim(),
        purpose,
        focus,
        length,
        status,
        source_review_acknowledged: acknowledgeSourceReview,
        gaps,
        sections: sections.map((section) => ({
          id: section.id || null,
          heading: section.heading.trim(),
          body: section.body.trim(),
        })),
      });
      setDetail(saved);
      setTitle(saved.title);
      setPurpose(saved.purpose);
      setFocus(saved.focus);
      setLength(saved.length);
      setStatus(saved.status);
      setGaps(saved.gaps);
      setAcknowledgeSourceReview(false);
      setSections(saved.sections.map((section) => ({ ...section, key: section.id })));
      await loadList(saved.id);
    } catch (err) {
      onError(err instanceof Error ? err.message : "保存报告失败");
    } finally {
      setBusy(false);
    }
  }

  async function createReport() {
    if (!draftTitle.trim() || busy) return;
    if (sourceDocuments.length === 0 && sourceNotes.length === 0) {
      onError("请至少选择一份资料或一条笔记");
      return;
    }
    setBusy(true);
    try {
      const report = await api.createReportFromSources(teamId, knowledgeBaseId, {
        title: draftTitle.trim(),
        purpose: draftPurpose,
        focus: draftFocus,
        length: draftLength,
        document_ids: sourceDocuments,
        note_ids: sourceNotes,
      });
      setCreating(false);
      await loadList(report.id);
      setSelectedId(report.id);
    } catch (err) {
      onError(err instanceof Error ? err.message : "创建报告失败");
    } finally {
      setBusy(false);
    }
  }

  function openCreate() {
    setDraftTitle("");
    setDraftPurpose("");
    setDraftFocus("");
    setDraftLength("medium");
    setSourceDocuments([]);
    setSourceNotes([]);
    setCreating(true);
  }

  async function requestSuggestion() {
    if (!detail || !suggestFor || !instruction.trim()) return;
    setSuggesting(true);
    try {
      setSuggestion(await api.suggestReportSection(
        teamId, knowledgeBaseId, detail.id, suggestFor.id, instruction.trim(),
      ));
    } catch (err) {
      onError(err instanceof Error ? err.message : "生成修改候选失败");
    } finally {
      setSuggesting(false);
    }
  }

  function adoptSuggestion() {
    if (!suggestFor || !suggestion) return;
    updateSection(suggestFor.key, { body: suggestion.suggestion });
    setSuggestFor(null);
    setSuggestion(null);
    setInstruction("");
  }

  async function removeReport() {
    if (!detail || !window.confirm(`删除报告“${detail.title}”？此操作无法恢复。`)) return;
    setBusy(true);
    try {
      await api.deleteReport(teamId, knowledgeBaseId, detail.id);
      setSelectedId(null);
      await loadList(null);
    } catch (err) {
      onError(err instanceof Error ? err.message : "删除报告失败");
    } finally {
      setBusy(false);
    }
  }

  function applyReportRequirement(requirement: SavedRequirementOut) {
    const payload = requirement.payload;
    setDraftPurpose(typeof payload.purpose === "string" ? payload.purpose : "");
    setDraftFocus(typeof payload.focus === "string" ? payload.focus : "");
    if (
      payload.length === "short" ||
      payload.length === "medium" ||
      payload.length === "long"
    ) {
      setDraftLength(payload.length);
    }
  }

  async function saveReportRequirement() {
    if (!requirementName.trim()) return;
    try {
      const saved = await api.createRequirement(teamId, knowledgeBaseId, {
        kind: "report",
        name: requirementName.trim(),
        payload: {
          purpose: draftPurpose,
          focus: draftFocus,
          length: draftLength,
        },
      });
      setReportRequirements((items) => [
        saved,
        ...items.filter((item) => item.id !== saved.id),
      ]);
      setSaveRequirementOpen(false);
      setRequirementName("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "保存报告要求失败");
    }
  }

  async function removeReportRequirement(requirement: SavedRequirementOut) {
    try {
      await api.deleteRequirement(teamId, knowledgeBaseId, requirement.id);
      setReportRequirements((items) => items.filter((item) => item.id !== requirement.id));
    } catch (err) {
      onError(err instanceof Error ? err.message : "删除报告要求失败");
    }
  }

  return (
    <section className="knowledge-products" aria-labelledby="reports-heading">
      <div className="panel__head knowledge-products__head">
        <div className="panel__heading">
          <Icon name="file" size={19} />
          <h2 id="reports-heading">报告</h2>
          <span className="panel__count">{reports.length}</span>
        </div>
        <button type="button" className="btn" onClick={openCreate}><Icon name="plus" size={16} />新建报告</button>
      </div>
      <div className="knowledge-products__layout">
        <aside className="knowledge-products__list" aria-label="报告列表">
          <label className="knowledge-products__search">
            <span className="sr-only">搜索报告</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索报告名称"
            />
          </label>
          {loading && reports.length === 0 ? (
            <p className="analysis-history__state" role="status">正在加载报告…</p>
          ) : reports.length === 0 ? (
            <p className="analysis-history__state">可从资料、笔记或已完成分析开始生成报告。</p>
          ) : filteredReports.length === 0 ? (
            <p className="analysis-history__state">没有匹配的报告。</p>
          ) : (
            <ul>
              {filteredReports.map((report) => (
                <li key={report.id}>
                  <button
                    type="button"
                    className={report.id === selectedId ? "is-active" : ""}
                    onClick={() => setSelectedId(report.id)}
                  >
                    <span className={`analysis-status ${report.status === "reviewed" ? "analysis-status--completed" : ""}`}>
                      {report.status === "reviewed" ? "已核对" : "草稿"}
                    </span>
                    <strong>{report.title}</strong>
                    <small>{report.section_count} 个章节 · {formatTime(report.updated_at)}</small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>
        <main className="knowledge-products__main">
          {dirty && <div className="knowledge-dirty" role="status"><Icon name="alert" size={15} />有未保存修改，切换前请先保存。</div>}
          {detail ? (
            <article className="knowledge-editor report-editor" aria-labelledby="report-editor-heading">
              <div className="knowledge-editor__head">
                <div>
                  <span className="knowledge-kicker">本人报告 · {detail.sources.length} 个来源</span>
                  <h2 id="report-editor-heading">{title || "未命名报告"}</h2>
                </div>
                <div className="report-editor__actions">
                  <select aria-label="报告状态" value={status} onChange={(event) => setStatus(event.target.value as ReportOut["status"])}>
                    <option value="draft">草稿</option>
                    <option value="reviewed">已核对</option>
                  </select>
                  <button type="button" className="btn" onClick={() => void saveDetail()} disabled={busy || !dirty}>
                    {busy ? "保存中…" : "保存"}
                  </button>
                </div>
              </div>
              <div className="report-downloads">
                {(["docx", "pdf", "xlsx", "csv"] as const).map((format) => (
                  <a
                    key={format}
                    className="btn btn--ghost"
                    href={api.reportExportUrl(teamId, knowledgeBaseId, detail.id, format)}
                  >
                    下载 {format.toUpperCase()}
                  </a>
                ))}
                <button type="button" className="btn btn--ghost" disabled={busy} onClick={() => void removeReport()}>
                  <Icon name="trash" size={15} />删除
                </button>
              </div>
              <div className="report-meta">
                <label><span>报告名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} /></label>
                <label><span>用途</span><input value={purpose} onChange={(event) => setPurpose(event.target.value)} maxLength={2000} /></label>
                <label><span>重点</span><input value={focus} onChange={(event) => setFocus(event.target.value)} maxLength={2000} /></label>
                <label><span>篇幅</span><select value={length} onChange={(event) => setLength(event.target.value as ReportOut["length"])}><option value="short">简短</option><option value="medium">适中</option><option value="long">详细</option></select></label>
              </div>
              {detail.review_note && <p className="analysis-partial-note">{detail.review_note}</p>}
              {detail.source_review_required && (
                <section className="report-source-review" aria-labelledby="report-source-review-heading">
                  <div>
                    <strong id="report-source-review-heading">来源已更新，需要复核</strong>
                    <p>{detail.source_review_note}</p>
                  </div>
                  <label>
                    <input
                      type="checkbox"
                      checked={acknowledgeSourceReview}
                      onChange={(event) => setAcknowledgeSourceReview(event.target.checked)}
                    />
                    <span>已核对当前来源和报告内容，保存后清除提示</span>
                  </label>
                </section>
              )}
              {gaps.length > 0 && (
                <section className="report-gaps" aria-labelledby="report-gaps-heading">
                  <h3 id="report-gaps-heading">待核实与材料缺口</h3>
                  <p>逐项核对后移除已处理事项；保存报告时一并更新清单。</p>
                  <ul>{gaps.map((item, index) => <li key={`${item.kind}-${index}`}><span>{item.kind === "source" ? "来源" : item.kind === "review" ? "待核实" : "缺口"}</span><p>{item.text}</p><button type="button" className="icon-button" aria-label="标记为已核对" title="标记为已核对" onClick={() => setGaps((items) => items.filter((_item, itemIndex) => itemIndex !== index))}><Icon name="check" size={15} /></button></li>)}</ul>
                </section>
              )}
              <section className="report-sections" aria-labelledby="report-sections-heading">
                <h3 id="report-sections-heading">章节</h3>
                {sections.map((section, index) => (
                  <article key={section.key} className="report-section-card">
                    <div className="report-section-card__head">
                      <span>{String(index + 1).padStart(2, "0")}</span>
                      <input aria-label={`第 ${index + 1} 章标题`} value={section.heading} onChange={(event) => updateSection(section.key, { heading: event.target.value })} maxLength={200} />
                      <button type="button" className="icon-button" aria-label="上移章节" disabled={index === 0} onClick={() => moveSection(index, -1)}>↑</button>
                      <button type="button" className="icon-button" aria-label="下移章节" disabled={index === sections.length - 1} onClick={() => moveSection(index, 1)}>↓</button>
                      <button type="button" className="icon-button" aria-label="删除章节" disabled={sections.length <= 1} onClick={() => removeSection(section.key)}><Icon name="trash" size={15} /></button>
                    </div>
                    <textarea aria-label={`${section.heading}正文`} value={section.body} onChange={(event) => updateSection(section.key, { body: event.target.value })} rows={9} maxLength={100000} />
                    <div className="report-section-card__foot">
                      <span>{section.citations.length ? `${section.citations.length} 条依据` : "本章没有直接引用"}</span>
                      <button type="button" className="btn btn--ghost" onClick={() => { setSuggestFor(section); setSuggestion(null); setInstruction(""); }}>修改候选</button>
                    </div>
                    {section.citations.length > 0 && (
                      <details className="report-citations">
                        <summary>查看本章原文依据</summary>
                        {section.citations.map((citation) => <blockquote key={citation.id}>{citation.document_title} · {citation.locator_label}<p>{citation.excerpt}</p></blockquote>)}
                      </details>
                    )}
                  </article>
                ))}
                <button type="button" className="btn btn--ghost" onClick={addSection}><Icon name="plus" size={15} />添加章节</button>
              </section>
              <ReportSources detail={detail} />
            </article>
          ) : (
            <div className="knowledge-empty">
              <Icon name="file" size={30} />
              <h3>{loading ? "正在加载报告…" : "还没有报告"}</h3>
              <p>从资料、笔记或已完成分析生成可编辑报告；页面和导出文件使用同一份保存正文。</p>
              {!loading && <button className="btn" type="button" onClick={openCreate}>新建报告</button>}
            </div>
          )}
        </main>
      </div>
      {creating && (
        <Modal title="新建报告" onClose={() => !busy && setCreating(false)} busy={busy}>
          <form className="knowledge-form" onSubmit={(event) => { event.preventDefault(); void createReport(); }}>
            <label><span>报告名称</span><input autoFocus value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} maxLength={200} required /></label>
            <label><span>用途</span><input value={draftPurpose} onChange={(event) => setDraftPurpose(event.target.value)} maxLength={2000} /></label>
            <label><span>重点</span><textarea value={draftFocus} onChange={(event) => setDraftFocus(event.target.value)} rows={3} maxLength={2000} /></label>
            <label><span>篇幅</span><select value={draftLength} onChange={(event) => setDraftLength(event.target.value as ReportOut["length"])}><option value="short">简短</option><option value="medium">适中</option><option value="long">详细</option></select></label>
            <section className="report-requirement-presets" aria-labelledby="report-requirement-presets-heading">
              <div>
                <strong id="report-requirement-presets-heading">常用报告要求</strong>
                <button type="button" className="btn btn--ghost" onClick={() => setSaveRequirementOpen(true)} disabled={busy}>保存当前用途与重点</button>
              </div>
              {reportRequirements.length === 0 ? <p>还没有保存的报告要求。</p> : <ul>{reportRequirements.map((item) => <li key={item.id}><button type="button" onClick={() => applyReportRequirement(item)}>{item.name}</button><button type="button" className="icon-button" aria-label={`删除报告要求 ${item.name}`} onClick={() => void removeReportRequirement(item)}><Icon name="trash" size={14} /></button></li>)}</ul>}
            </section>
            <fieldset className="knowledge-source-picker"><legend>资料</legend>{readyDocuments.map((document) => <label key={document.id}><input type="checkbox" checked={sourceDocuments.includes(document.id)} onChange={(event) => setSourceDocuments((items) => event.target.checked ? [...items, document.id] : items.filter((id) => id !== document.id))} /><span>{documentLabel(document)}</span></label>)}</fieldset>
            <fieldset className="knowledge-source-picker"><legend>笔记</legend>{notes.map((note) => <label key={note.id}><input type="checkbox" checked={sourceNotes.includes(note.id)} onChange={(event) => setSourceNotes((items) => event.target.checked ? [...items, note.id] : items.filter((id) => id !== note.id))} /><span>{note.title}</span></label>)}</fieldset>
            <div className="maintenance-actions"><button type="button" className="btn btn--ghost" disabled={busy} onClick={() => setCreating(false)}>取消</button><button className="btn" disabled={busy || !draftTitle.trim() || (sourceDocuments.length === 0 && sourceNotes.length === 0)}>{busy ? "生成中…" : "生成可编辑报告"}</button></div>
          </form>
        </Modal>
      )}
      {suggestFor && (
        <Modal title="单章节修改候选" onClose={() => !suggesting && setSuggestFor(null)} busy={suggesting}>
          <div className="knowledge-form">
            <p className="maintenance-hint">先生成候选，预览后决定是否采用；不会自动修改已保存章节。</p>
            <label><span>修改要求</span><textarea autoFocus rows={4} value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="例如：改得更简洁，保留全部关键条件" /></label>
            {suggestion && <section className="report-suggestion"><span>{suggestion.provider === "http" ? "AI 候选" : "依据核对候选"}</span><p>{suggestion.suggestion}</p><small>{suggestion.rationale}</small></section>}
            <div className="maintenance-actions">
              <button type="button" className="btn btn--ghost" disabled={suggesting} onClick={() => setSuggestFor(null)}>关闭</button>
              {!suggestion ? <button type="button" className="btn" disabled={suggesting || instruction.trim().length < 2} onClick={() => void requestSuggestion()}>{suggesting ? "生成中…" : "生成候选"}</button> : <button type="button" className="btn" onClick={adoptSuggestion}>采用并设为未保存</button>}
            </div>
          </div>
        </Modal>
      )}
      {saveRequirementOpen && (
        <Modal title="保存常用报告要求" onClose={() => setSaveRequirementOpen(false)}>
          <form className="knowledge-form" onSubmit={(event) => { event.preventDefault(); void saveReportRequirement(); }}>
            <p className="maintenance-hint">保存当前新建报告中的用途、重点和篇幅；同名要求会更新原记录。</p>
            <label><span>名称</span><input autoFocus value={requirementName} onChange={(event) => setRequirementName(event.target.value)} maxLength={120} required /></label>
            <div className="maintenance-actions">
              <button type="button" className="btn btn--ghost" onClick={() => setSaveRequirementOpen(false)}>取消</button>
              <button className="btn" disabled={!requirementName.trim()}>保存</button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}

function ReportSources({ detail }: { detail: ReportOut }) {
  if (detail.sources.length === 0) return null;
  return (
    <section className="report-sources" aria-labelledby="report-sources-heading">
      <h3 id="report-sources-heading">来源</h3>
      {detail.sources.map((source) => <div key={source.id}><Icon name={source.source_type === "note" ? "edit" : "file"} size={15} /><span>{source.title_snapshot}</span><small>{source.available ? "可用" : "保存时来源已不可用"}</small></div>)}
    </section>
  );
}
