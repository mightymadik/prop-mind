import { normalizeComplex, normalizeEvent, normalizePayload } from "./normalize";

test("missing optional fields get defaults, cards without id and broken groups are dropped", () => {
  expect(normalizeComplex({ source_id: "cx-1", groups: [{ rooms: 2, count: 3 }, { nope: 1 }] })).toMatchObject({
    name: "", feature_labels: [], payment_labels: [], description: "",
    groups: [{ rooms: 2, count: 3, area_min: 0, price_max: 0 }],
  });
  expect(normalizeComplex({ name: "x" })).toBeNull();
  expect(normalizeEvent("filters", { filters: {} })).toEqual({ name: "filters", data: { filters: {}, chips: [] } });
  expect(normalizeEvent("results", { results: [{ source_id: "a" }, { nope: 1 }] })?.data).toMatchObject({
    relaxed: [], results: [{ source_id: "a" }],
  });
  expect(normalizeEvent("something-new", {})).toBeNull();
  expect(normalizePayload({ clarification: "?" })).toMatchObject({ results: [], chips: [], clarification: "?" });
});
