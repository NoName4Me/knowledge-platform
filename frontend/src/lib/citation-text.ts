import type { Turn } from "./api";
import type { EvidenceSource } from "./evidence-context";

/** 引用编号来自已保存的回答标记；不把稀疏编号误当作数组下标。 */
export type CitationFragment = { text: string; numbers: number[] };
export function referenceNumbers(answer: string): number[] {
  return [...new Set([...answer.matchAll(/\[(\d+)\](?!\()/g)]
    .map((match) => Number(match[1])).filter((number) => Number.isSafeInteger(number) && number > 0))]
    .sort((a, b) => a - b);
}
export function citationNumber(answer: string, count: number, index: number, number?: number | null): number {
  if (number != null && number > 0) return number;
  const references = referenceNumbers(answer);
  return references.length === count ? references[index] : index + 1;
}

/** 每段文字关联它后面的引用组；无标记的文字不作证据关联。保留原始文本，不执行 HTML。 */
export function citationFragments(answer: string, available: number[]): CitationFragment[] {
  const valid = new Set(available);
  const fragments: CitationFragment[] = [];
  const marker = /\[(\d+)\](?!\()/g;
  let start = 0;
  for (const match of answer.matchAll(marker)) {
    const number = Number(match[1]);
    if (!valid.has(number)) continue;
    const text = answer.slice(start, match.index);
    const previous = fragments.at(-1);
    if (!text.trim() && previous?.numbers.length) {
      if (!previous.numbers.includes(number)) previous.numbers.push(number);
      previous.text += text;
    } else if (!text.trim() && previous) {
      previous.numbers.push(number);
      previous.text += text;
    } else {
      // 引用写在句末时只高亮最近的句段；引用写在独立一行时仍关联前文。
      const last = text.trimEnd().replace(/[。！？.!?；;]$/, "");
      const boundary = Math.max(last.lastIndexOf("。"), last.lastIndexOf("！"),
        last.lastIndexOf("？"), last.lastIndexOf("\n"));
      const leading = boundary >= 0 ? text.slice(0, boundary + 1) : "";
      if (leading) fragments.push({ text: leading, numbers: [] });
      fragments.push({ text: text.slice(leading.length), numbers: [number] });
    }
    start = match.index + match[0].length;
  }
  if (start < answer.length || fragments.length === 0) fragments.push({ text: answer.slice(start), numbers: [] });
  return fragments;
}

export function turnEvidence(turn: Turn): EvidenceSource[] {
  return turn.citations.map((citation, index) => {
    const number = citationNumber(turn.answer ?? "", turn.citations.length, index, citation.number);
    return { id: String(number), number, documentId: citation.document_id, title: citation.document_title,
      locator: citation.locator_label, excerpt: citation.excerpt };
  });
}

/** 同一张表的多个行片段共享一个依据入口；旧引用没有结构时保持逐条文本。 */
export function groupEvidenceTables(sources: EvidenceSource[]): EvidenceSource[] {
  const grouped = new Map<string, EvidenceSource>();
  for (const source of sources) {
    const table = source.table;
    if (!table) {
      grouped.set(source.id, source);
      continue;
    }
    const key = `${source.documentId}:${table.number}`;
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, {
        ...source,
        id: key,
        number: source.number,
        locator: `第 ${table.number} 张表 · ${table.rows.length} 行`,
      });
      continue;
    }
    const rows = [...(existing.table?.rows ?? []), ...table.rows];
    const headers =
      existing.table?.headers?.length
        ? existing.table.headers
        : table.headers;
    grouped.set(key, {
      ...existing,
      locator: `第 ${table.number} 张表 · ${new Set(rows.map((row) => row.key)).size} 行`,
      table: {
        number: table.number,
        headers,
        rows: [...new Map(rows.map((row) => [row.key, row])).values()],
      },
    });
  }
  return [...grouped.values()];
}
