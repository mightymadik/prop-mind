import type { ChatMode, ChatTurn, SearchEvent, SearchPayload } from "../types";
import { normalizePayload } from "./normalize";
import { readEvents } from "./sse";

export class SearchError extends Error {}

function errorText(status: number, detail: unknown): string {
  if ((status === 429 || status === 403 || status === 503) && typeof detail === "string") return detail;
  if (status === 503) return "Сервис временно недоступен.";
  return "Не получилось выполнить поиск. Попробуйте ещё раз.";
}

async function ensureOk(resp: Response): Promise<void> {
  if (resp.ok) return;
  let detail: unknown = null;
  try {
    detail = (await resp.json()).detail;
  } catch {
    /* не JSON */
  }
  throw new SearchError(errorText(resp.status, detail));
}

// v1 — /search, каждый запрос независимый; v2 — /agent с историей диалога
export interface Ask {
  mode: ChatMode;
  query: string;
  history: ChatTurn[];
  siteKey: string | null;
}

function request(a: Ask, stream: boolean): Promise<Response> {
  const body = a.mode === "v2" ? { query: a.query, history: a.history, site_key: a.siteKey } : { query: a.query, site_key: a.siteKey };
  return fetch(a.mode === "v2" ? "/agent" : "/search", {
    method: "POST",
    headers: stream ? { "content-type": "application/json", accept: "text/event-stream" } : { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function searchStream(a: Ask, onEvent: (e: SearchEvent) => void): Promise<void> {
  const resp = await request(a, true);
  await ensureOk(resp);
  if (!resp.body) throw new SearchError(errorText(0, null));
  for await (const e of readEvents(resp.body)) onEvent(e);
}

export async function searchOnce(a: Ask): Promise<SearchPayload> {
  const resp = await request(a, false);
  await ensureOk(resp);
  return normalizePayload(await resp.json());
}

// Режим страницы (chat.mode на сервере); ошибка или нет ответа за 3 с → v1: он работает без агента,
// а зависший запрос режима не должен блокировать отправку сообщений.
export async function chatMode(timeoutMs = 3000): Promise<ChatMode> {
  try {
    const resp = await fetch("/chat/config", { signal: AbortSignal.timeout(timeoutMs) });
    const d = resp.ok ? await resp.json() : null;
    return d?.mode === "v2" ? "v2" : "v1";
  } catch {
    return "v1";
  }
}

// Сколько ЖК и свободных квартир в базе — для строки под заголовком; любая ошибка → null, строку не показываем.
export async function catalogCounts(): Promise<{ complexes: number; units: number } | null> {
  try {
    const resp = await fetch("/health");
    const d = resp.ok ? await resp.json() : null;
    const ok = (n: unknown): n is number => Number.isInteger(n) && (n as number) > 0;
    return ok(d?.complexes) && ok(d?.free_units) ? { complexes: d.complexes, units: d.free_units } : null;
  } catch {
    return null;
  }
}
