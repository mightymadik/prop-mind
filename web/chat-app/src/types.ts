// Формат событий и ответа POST /search (см. app/service.py).
export interface Listing {
  source_id: string;
  price_kzt: number;
  rooms: number | null;
  area_m2: number | null;
  district: string | null;
  condition: string | null;
  condition_label: string | null;
  features: string[];
  feature_labels: string[];
  description: string;
}

export type SearchEvent =
  | { name: "status"; data: { stage: string } }
  | { name: "filters"; data: { filters: Record<string, unknown>; chips: string[] } }
  | { name: "results"; data: { relaxed: string[]; results: Listing[] } }
  | { name: "delta"; data: { text: string } }
  | { name: "answer"; data: { text: string; invalid_refs: number[]; cited: string[] } }
  | { name: "clarification"; data: { text: string } }
  | { name: "error"; data: { error: string } }
  | { name: "done"; data: Record<string, unknown> };

export interface SearchPayload {
  chips?: string[];
  relaxed?: string[];
  results?: Listing[];
  answer?: string;
  clarification?: string;
}
