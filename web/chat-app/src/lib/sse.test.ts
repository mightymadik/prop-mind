import { readEvents, takeEvents } from "./sse";

test("events split across chunks and several events in one chunk", () => {
  const a = takeEvents('event: status\ndata: {"stage":"parse"}\n\nevent: filters\ndata: {"chi');
  expect(a.events).toEqual([{ name: "status", data: { stage: "parse" } }]);
  const b = takeEvents(a.rest + 'ps":["Алматы"],"filters":{}}\n\n');
  expect(b.events).toEqual([{ name: "filters", data: { chips: ["Алматы"], filters: {} } }]);
  expect(b.rest).toBe("");
});

test("readEvents decodes a UTF-8 char cut between chunks", async () => {
  const bytes = new TextEncoder().encode('event: delta\ndata: {"text":"Двор"}\n\n');
  const cut = 30; // посреди кириллической буквы
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(bytes.slice(0, cut));
      c.enqueue(bytes.slice(cut));
      c.close();
    },
  });
  const got = [];
  for await (const e of readEvents(body)) got.push(e);
  expect(got).toEqual([{ name: "delta", data: { text: "Двор" } }]);
});
