import type { Listing } from "../types";
import { segmentAnswer } from "./refs";

const L = (id: string) => ({ source_id: id }) as Listing;
const results = [L("syn-1"), L("syn-2")];

test("numbers while streaming and source_id in the final text map to cards", () => {
  expect(segmentAnswer("Лучше [2], затем [syn-1].", results)).toEqual([
    { kind: "text", text: "Лучше " },
    { kind: "ref", index: 1, sourceId: "syn-2" },
    { kind: "text", text: ", затем " },
    { kind: "ref", index: 0, sourceId: "syn-1" },
    { kind: "text", text: "." },
  ]);
});

test("brackets that are not listings stay text", () => {
  expect(segmentAnswer("Номер [7] и [пример].", results)).toEqual([{ kind: "text", text: "Номер [7] и [пример]." }]);
});
