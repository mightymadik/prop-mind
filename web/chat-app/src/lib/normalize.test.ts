import { normalizeEvent, normalizeListing, normalizePayload } from "./normalize";

test("missing optional fields get defaults, cards without id are dropped", () => {
  expect(normalizeListing({ source_id: "syn-1", price_kzt: 1, features: ["parking"] })).toMatchObject({
    feature_labels: ["parking"], condition_label: null, description: "", rooms: null,
  });
  expect(normalizeListing({ price_kzt: 1 })).toBeNull();
  expect(normalizeEvent("filters", { filters: {} })).toEqual({ name: "filters", data: { filters: {}, chips: [] } });
  expect(normalizeEvent("results", { results: [{ source_id: "a" }, { nope: 1 }] })?.data).toMatchObject({
    relaxed: [], results: [{ source_id: "a" }],
  });
  expect(normalizeEvent("something-new", {})).toBeNull();
  expect(normalizePayload({ clarification: "?" })).toMatchObject({ results: [], chips: [], clarification: "?" });
});
