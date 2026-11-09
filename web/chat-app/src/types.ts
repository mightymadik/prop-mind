// Формат событий и ответа POST /search (см. app/service.py).
// Свободные квартиры ЖК с одним числом комнат, подходящие под запрос.
export interface UnitGroup {
  rooms: number;
  count: number;
  area_min: number;
  area_max: number;
  price_min: number;
  price_max: number;
}

// Карточка ЖК (новостройки): номер [n] в ответе ссылается на ЖК.
export interface Complex {
  source_id: string;
  name: string;
  city: string;
  district: string;
  developer: string;
  class_label: string;
  completion_label: string;
  feature_labels: string[];
  payment_labels: string[];
  description: string;
  groups: UnitGroup[];
}

export type SearchEvent =
  | { name: "status"; data: { stage: string } }
  | { name: "filters"; data: { filters: Record<string, unknown>; chips: string[] } }
  | { name: "results"; data: { relaxed: string[]; results: Complex[] } }
  | { name: "delta"; data: { text: string } }
  | { name: "answer"; data: { text: string; invalid_refs: number[]; cited: string[] } }
  | { name: "clarification"; data: { text: string } }
  | { name: "error"; data: { error: string } }
  | { name: "done"; data: Record<string, unknown> };

export interface SearchPayload {
  chips?: string[];
  relaxed?: string[];
  results?: Complex[];
  answer?: string;
  clarification?: string;
}
