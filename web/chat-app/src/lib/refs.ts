import type { Listing } from "../types";

export type Segment = { kind: "text"; text: string } | { kind: "ref"; index: number; sourceId: string };

// [3] — во время стрима (номер), [syn-00012] — в финальном тексте (source_id)
const REF = /\[([^\]\s]+)\]/g;

/** Текст ответа → куски текста и ссылки на карточки. Скобки, не указывающие на объект, остаются текстом. */
export function segmentAnswer(text: string, results: Listing[]): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(REF)) {
    const token = m[1];
    const index = /^\d+$/.test(token)
      ? Number(token) - 1
      : results.findIndex((r) => r.source_id === token);
    if (index < 0 || index >= results.length) continue;
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    out.push({ kind: "ref", index, sourceId: results[index].source_id });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}
