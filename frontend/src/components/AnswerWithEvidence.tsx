import { useEffect, useMemo, useRef } from "react";
import type { Turn } from "../lib/api";
import { citationFragments, referenceNumbers, turnEvidence } from "../lib/citation-text";
import { useEvidence, type EvidenceSource } from "../lib/evidence-context";
import { Icon } from "./Icon";

export function AnswerWithEvidence({ turn, loadSources }: { turn: Turn; loadSources: () => Promise<EvidenceSource[]> }) {
  const evidence = useEvidence();
  const ref = useRef<HTMLParagraphElement>(null);
  const sources = turnEvidence(turn);
  const fragments = citationFragments(turn.answer ?? "", sources.map((source) => source.number));
  const active = evidence.data?.scope === turn.id ? evidence.activeId : null;
  useEffect(() => {
    if (!active) return;
    const el = [...ref.current?.querySelectorAll<HTMLElement>("[data-reference-numbers]") ?? []]
      .find((item) => item.dataset.referenceNumbers?.split(" ").includes(active));
    if (!el) return;
    const list = el.closest(".turn-list");
    if (!list) return;
    const bounds = el.getBoundingClientRect();
    const viewport = list.getBoundingClientRect();
    if (bounds.top < viewport.top || bounds.bottom > viewport.bottom) el.scrollIntoView({ block: "nearest", behavior: "auto" });
  }, [active]);
  function open(number?: number) {
    evidence.open({ scope: turn.id, title: "原文引用", selected: number ? String(number) : undefined, load: loadSources });
  }
  return <>
    <p className="turn__answer" ref={ref}>{fragments.map((fragment, index) => <span key={index}>
      <span data-reference-numbers={fragment.numbers.join(" ")} className={active && fragment.numbers.includes(Number(active)) ? "answer-fragment is-highlighted" : "answer-fragment"}>{fragment.text}</span>
      {fragment.numbers.map((number) => <sup className="reference-mark" key={number}><button type="button" onClick={() => open(number)} aria-label={`查看引用 ${number}`} aria-expanded={evidence.data?.scope === turn.id && evidence.activeId === String(number)}>{number}</button></sup>)}
    </span>)}</p>
    {sources.length > 0 && <button type="button" className="view-evidence" aria-label={`查看全部 ${sources.length} 条引用`} onClick={() => open()} aria-expanded={evidence.data?.scope === turn.id}><Icon name="quote" size={15} />查看引用<span>{sources.length}</span><Icon name="chevron" size={14} /></button>}
  </>;
}

export function StreamingAnswer({ text }: { text: string }) {
  const fragments = useMemo(() => citationFragments(text, referenceNumbers(text)), [text]);
  return <p className="turn__answer" aria-live="polite">{fragments.map((fragment, index) => <span key={index}>{fragment.text}{fragment.numbers.map((number) => <sup className="reference-mark is-pending" key={number}>{number}</sup>)}</span>)}</p>;
}
