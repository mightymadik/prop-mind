import { chatReducer, emptyChat, history, type Chat } from "./state";
import type { Complex, SearchEvent } from "./types";

const ev = (c: Chat, id: string, event: SearchEvent) => chatReducer(c, { type: "event", id, event });

test("stream: stages → chips → cards → deltas → answer → done, per turn", () => {
  let c = chatReducer(emptyChat, { type: "ask", id: "t1", query: "двушка", stream: true, mode: "v1" });
  expect(c.turns[0].reply).toMatchObject({ busy: true, stage: "parse", results: null });
  c = ev(c, "t1", { name: "status", data: { stage: "rerank" } });
  c = ev(c, "t1", { name: "filters", data: { filters: {}, chips: ["Алматы"] } });
  c = ev(c, "t1", { name: "results", data: { relaxed: [], results: [{ source_id: "syn-1" } as Complex] } });
  c = ev(c, "t1", { name: "delta", data: { text: "Подходит " } });
  c = ev(c, "t1", { name: "delta", data: { text: "[1]" } });
  expect(c.turns[0].reply).toMatchObject({ stage: "rerank", chips: ["Алматы"], answer: "Подходит [1]" });
  c = ev(c, "t1", { name: "done", data: {} });
  expect(c.turns[0].reply).toMatchObject({ busy: false, stage: null });
});

test("events update only their own turn; history is kept", () => {
  let c = chatReducer(emptyChat, { type: "ask", id: "t1", query: "раз", stream: true, mode: "v1" });
  c = ev(c, "t1", { name: "done", data: {} });
  c = chatReducer(c, { type: "ask", id: "t2", query: "два", stream: true, mode: "v1" });
  c = ev(c, "t2", { name: "delta", data: { text: "ответ 2" } });
  expect(c.turns.map((t) => [t.query, t.reply.answer])).toEqual([["раз", ""], ["два", "ответ 2"]]);
});

test("clarification, error, unknown stage, non-stream payload", () => {
  const c = chatReducer(emptyChat, { type: "ask", id: "t", query: "q", stream: true, mode: "v1" });
  expect(ev(c, "t", { name: "clarification", data: { text: "Метро только в Алматы." } }).turns[0].reply).toMatchObject({
    clarification: "Метро только в Алматы.", results: [],
  });
  expect(ev(c, "t", { name: "error", data: { error: "x" } }).turns[0].reply.error).toBeTruthy();
  expect(ev(c, "t", { name: "status", data: { stage: "???" } }).turns[0].reply.stage).toBe("parse");
  expect(chatReducer(c, { type: "fail", id: "t", text: "Лимит" }).turns[0].reply).toMatchObject({ busy: false, error: "Лимит" });
  const once = chatReducer(chatReducer(emptyChat, { type: "ask", id: "o", query: "q", stream: false, mode: "v1" }), {
    type: "payload", id: "o", payload: { chips: ["Астана"], results: [], answer: "ok" },
  });
  expect(once.turns[0].reply).toMatchObject({ busy: false, chips: ["Астана"], answer: "ok" });
});

test("end without done: partial answer is kept, empty stream becomes an error, after done nothing changes", () => {
  const ask = (c: Chat) => chatReducer(c, { type: "ask", id: "t1", query: "q", stream: true, mode: "v1" });
  const partial = chatReducer(ask(emptyChat), { type: "event", id: "t1", event: { name: "delta", data: { text: "Начало" } } });
  const ended = chatReducer(partial, { type: "end", id: "t1" }).turns[0].reply;
  expect(ended).toMatchObject({ busy: false, answer: "Начало", error: null });

  const empty = chatReducer(ask(emptyChat), { type: "end", id: "t1" }).turns[0].reply;
  expect(empty).toMatchObject({ busy: false, results: [], error: "Соединение прервалось до ответа. Попробуйте ещё раз." });

  const done = chatReducer(partial, { type: "event", id: "t1", event: { name: "done", data: {} } });
  expect(chatReducer(done, { type: "end", id: "t1" }).turns[0].reply).toBe(done.turns[0].reply);
});

test("history: only finished turns with a signed reply, last 20 items", () => {
  const turn = { role: "assistant" as const, text: "ok", source_ids: [], ts: 1, sig: "s" };
  let c = emptyChat;
  for (let i = 0; i < 12; i++) {
    c = chatReducer(c, { type: "ask", id: `t${i}`, query: `q${i}`, stream: true, mode: "v2" });
    c = chatReducer(c, { type: "event", id: `t${i}`, event: { name: "answer", data: { text: "ok", invalid_refs: [], cited: [], clarified: false, turn } } });
    c = chatReducer(c, { type: "event", id: `t${i}`, event: { name: "done", data: {} } });
  }
  c = chatReducer(c, { type: "ask", id: "busy", query: "в работе", stream: true, mode: "v2" }); // ещё идёт — не в истории
  const h = history(c);
  expect(h).toHaveLength(20);
  expect(h[0]).toEqual({ role: "user", text: "q2" });
  expect(h.at(-1)).toEqual(turn);
  expect(chatReducer(c, { type: "reset" })).toEqual(emptyChat);
});
