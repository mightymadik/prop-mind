import type { Listing, SearchEvent, SearchPayload } from "../types";

// Данные сервера — на входе к значениям по умолчанию: страница не должна падать, если версия API
// отстаёт или опережает фронтенд (так и было: старый контейнер без chips/labels → белый экран).

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

export function normalizeListing(raw: unknown): Listing | null {
  const r = (raw ?? {}) as Record<string, unknown>;
  const id = str(r.source_id);
  if (!id) return null; // без id карточку не на что сослаться — отбрасываем
  const features = strs(r.features);
  return {
    source_id: id,
    price_kzt: num(r.price_kzt) ?? 0,
    rooms: num(r.rooms),
    area_m2: num(r.area_m2),
    district: str(r.district),
    condition: str(r.condition),
    condition_label: str(r.condition_label),
    features,
    feature_labels: Array.isArray(r.feature_labels) ? strs(r.feature_labels) : features,
    description: str(r.description) ?? "",
  };
}

export function normalizeListings(v: unknown): Listing[] {
  return Array.isArray(v) ? v.map(normalizeListing).filter((x): x is Listing => x !== null) : [];
}

/** Событие SSE → строго типизированное; неизвестные события — null (пропускаем). */
export function normalizeEvent(name: string, raw: unknown): SearchEvent | null {
  const d = (raw ?? {}) as Record<string, unknown>;
  switch (name) {
    case "status": return { name, data: { stage: str(d.stage) ?? "" } };
    case "filters": return { name, data: { filters: (d.filters as Record<string, unknown>) ?? {}, chips: strs(d.chips) } };
    case "results": return { name, data: { relaxed: strs(d.relaxed), results: normalizeListings(d.results) } };
    case "delta": return { name, data: { text: str(d.text) ?? "" } };
    case "answer": return { name, data: { text: str(d.text) ?? "", invalid_refs: [], cited: strs(d.cited) } };
    case "clarification": return { name, data: { text: str(d.text) ?? "" } };
    case "error": return { name, data: { error: str(d.error) ?? "" } };
    case "done": return { name, data: d };
    default: return null;
  }
}

export function normalizePayload(raw: unknown): SearchPayload {
  const d = (raw ?? {}) as Record<string, unknown>;
  return {
    chips: strs(d.chips),
    relaxed: strs(d.relaxed),
    results: normalizeListings(d.results),
    answer: str(d.answer) ?? "",
    clarification: str(d.clarification) ?? undefined,
  };
}
