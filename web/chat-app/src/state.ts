import type { Complex, SearchEvent, SearchPayload } from "./types";

// Этапы ответа в порядке прохождения; подписи — в StageSteps.
export const STAGE_ORDER = ["parse", "search", "rerank", "answer"] as const;
export type Stage = (typeof STAGE_ORDER)[number];

export interface Reply {
  busy: boolean;
  stage: Stage | null; // текущий этап; null — ещё не начался или уже закончился
  chips: string[];
  results: Complex[] | null; // null — ждём выдачу (заглушки), [] — пусто
  answer: string;
  clarification: string | null; // вопрос парсера — показываем как вопрос ассистента
  error: string | null;
}

export interface Turn {
  id: string;
  query: string;
  reply: Reply;
}

export interface Chat {
  turns: Turn[];
}

export const emptyChat: Chat = { turns: [] };

const newReply = (stream: boolean): Reply => ({
  busy: true,
  stage: stream ? "parse" : null,
  chips: [],
  results: null,
  answer: "",
  clarification: null,
  error: null,
});

export type Action =
  | { type: "ask"; id: string; query: string; stream: boolean }
  | { type: "event"; id: string; event: SearchEvent }
  | { type: "end"; id: string } // поток закрылся — с done или без (обрыв на прокси, падение сервера)
  | { type: "payload"; id: string; payload: SearchPayload }
  | { type: "fail"; id: string; text: string };

const GENERIC_ERROR = "Не получилось выполнить поиск. Попробуйте ещё раз.";
const CUT_ERROR = "Соединение прервалось до ответа. Попробуйте ещё раз.";

/** Ответ одного хода на событие сервера (чистая функция). */
export function replyReducer(r: Reply, a: Exclude<Action, { type: "ask" }>): Reply {
  switch (a.type) {
    case "payload": {
      const p = a.payload;
      const base = { ...r, busy: false, stage: null, chips: p.chips ?? [] };
      if (p.clarification) return { ...base, results: [], clarification: p.clarification };
      return { ...base, results: p.results ?? [], answer: p.answer ?? "" };
    }
    case "fail":
      return { ...r, busy: false, stage: null, results: r.results ?? [], error: a.text };
    case "end": {
      if (!r.busy) return r; // done уже пришёл
      const got = r.answer || r.clarification || (r.results !== null && r.results.length > 0);
      return { ...r, busy: false, stage: null, results: r.results ?? [], error: got ? r.error : CUT_ERROR };
    }
    case "event": {
      const e = a.event;
      switch (e.name) {
        case "status":
          return { ...r, stage: (STAGE_ORDER as readonly string[]).includes(e.data.stage) ? (e.data.stage as Stage) : r.stage };
        case "filters":
          return { ...r, chips: e.data.chips };
        case "results":
          return { ...r, results: e.data.results };
        case "delta":
          return { ...r, answer: r.answer + e.data.text };
        case "answer":
          return { ...r, answer: e.data.text };
        case "clarification":
          return { ...r, results: [], clarification: e.data.text };
        case "error":
          return { ...r, results: r.results ?? [], error: GENERIC_ERROR };
        case "done":
          return { ...r, busy: false, stage: null, results: r.results ?? [] };
      }
    }
  }
}

export function chatReducer(c: Chat, a: Action): Chat {
  if (a.type === "ask") return { turns: [...c.turns, { id: a.id, query: a.query, reply: newReply(a.stream) }] };
  return { turns: c.turns.map((t) => (t.id === a.id ? { ...t, reply: replyReducer(t.reply, a) } : t)) };
}
