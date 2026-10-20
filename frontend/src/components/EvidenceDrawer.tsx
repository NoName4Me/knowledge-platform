import { useEffect, useRef, useId } from "react";
import { useEvidence } from "../lib/evidence-context";
import { Icon } from "./Icon";

export function EvidenceDrawer({ teamId }: { teamId: string }) {
  const { data, activeId, open, close, select, hover } = useEvidence();
  const panelRef = useRef<HTMLElement>(null);
  const titleId = useId();
  const scope = data?.scope;
  const loading = data?.loading;
  const target = data?.selected;
  useEffect(() => {
    if (!scope) return;
    panelRef.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); close(); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [scope, close]);
  useEffect(() => {
    if (!scope || loading || !target) return;
    const item = [...panelRef.current?.querySelectorAll<HTMLElement>("[data-source-id]") ?? []]
      .find((element) => element.dataset.sourceId === target);
    item?.scrollIntoView({ block: "nearest", behavior: "auto" });
  }, [scope, loading, target]);
  if (!data) return null;
  return <aside ref={panelRef} className="evidence-drawer" aria-labelledby={titleId}>
    <div className="evidence-drawer__head"><div><p>回到原文核对</p><h2 id={titleId}>{data.title}</h2></div>
      <button className="icon-button" type="button" aria-label="关闭引用抽屉" onClick={close}><Icon name="close" size={19} /></button></div>
    <div className="evidence-drawer__body">
      {data.loading ? <p className="panel__empty" role="status">正在读取原文引用…</p> : data.error ? <div role="alert" className="evidence-error"><p>{data.error}</p><button type="button" className="btn btn--ghost" onClick={() => open(data)}>重试</button></div> : data.sources.length === 0 ? <p className="panel__empty">引用已失效或暂无可查看的原文。</p> : <>
        <p className="evidence-drawer__hint">悬浮或聚焦引用，可在回答中对照对应内容。</p>
        <ol className="evidence-source-list">{data.sources.map((source) => <li key={source.id}>
          <article tabIndex={0} data-source-id={source.id} aria-label={`引用 ${source.number}：${source.title}`}
            className={`evidence-source${source.id === activeId ? " is-active" : ""}`}
            onMouseEnter={() => hover(source.id)} onMouseLeave={() => hover(null)}
            onFocus={() => hover(source.id)} onBlur={() => hover(null)} onClick={() => select(source.id)}
            onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(source.id); } }}>
            <div className="evidence-source__head"><span>{source.number}</span><div><h3>{source.title}</h3><p>{source.locator}</p></div></div>
            {source.excerpt ? <blockquote>{source.excerpt}</blockquote> : <p className="evidence-source__deleted">原始资料已删除或不再可用，保留来源信息。</p>}
            {source.excerpt && <a href={`/api/teams/${teamId}/documents/${source.documentId}/content`} target="_blank" rel="noopener noreferrer">打开原文件 <Icon name="external" size={13} /></a>}
          </article>
        </li>)}</ol>
      </>}
    </div>
  </aside>;
}
