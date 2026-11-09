import type { Complex, SearchEvent, SearchPayload, UnitGroup } from "../types";

// Данные сервера — на входе к значениям по умолчанию: страница не должна падать, если версия API
// отстаёт или опережает фронтенд (так и было: старый контейнер без chips/labels → белый экран).

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

function normalizeGroup(raw: unknown): UnitGroup | null {
  const g = (raw ?? {}) as Record<string, unknown>;
  const rooms = num(g.rooms);
  if (rooms === null) return null;
  return {
    rooms,
    count: num(g.count) ?? 0,
    area_min: num(g.area_min) ?? 0,
    area_max: num(g.area_max) ?? 0,
    price_min: num(g.price_min) ?? 0,
    price_max: num(g.price_max) ?? 0,
  };
}

export function normalizeComplex(raw: unknown): Complex | null {
  const r = (raw ?? {}) as Record<string, unknown>;
  const id = str(r.source_id);
  if (!id) return null; // без id карточку не на что сослаться — отбрасываем
  return {
    source_id: id,
    name: str(r.name) ?? "",
    city: str(r.city) ?? "",
    district: str(r.district) ?? "",
    developer: str(r.developer) ?? "",
    class_label: str(r.class_label) ?? "",
    completion_label: str(r.completion_label) ?? "",
    feature_labels: strs(r.feature_labels),
    payment_labels: strs(r.payment_labels),
    description: str(r.description) ?? "",
    groups: Array.isArray(r.groups) ? r.groups.map(normalizeGroup).filter((g): g is UnitGroup => g !== null) : [],
  };
}

export function normalizeComplexes(v: unknown): Complex[] {
  return Array.isArray(v) ? v.map(normalizeComplex).filter((x): x is Complex => x !== null) : [];
}

/** Событие SSE → строго типизированное; неизвестные события — null (пропускаем). */
export function normalizeEvent(name: string, raw: unknown): SearchEvent | null {
  const d = (raw ?? {}) as Record<string, unknown>;
  switch (name) {
    case "status": return { name, data: { stage: str(d.stage) ?? "" } };
    case "filters": return { name, data: { filters: (d.filters as Record<string, unknown>) ?? {}, chips: strs(d.chips) } };
    case "results": return { name, data: { relaxed: strs(d.relaxed), results: normalizeComplexes(d.results) } };
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
    results: normalizeComplexes(d.results),
    answer: str(d.answer) ?? "",
    clarification: str(d.clarification) ?? undefined,
  };
}
