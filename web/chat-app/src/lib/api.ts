import type { SearchEvent, SearchPayload } from "../types";
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

function body(query: string, siteKey: string | null): string {
  return JSON.stringify({ query, site_key: siteKey });
}

export async function searchStream(query: string, siteKey: string | null, onEvent: (e: SearchEvent) => void): Promise<void> {
  const resp = await fetch("/search", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: body(query, siteKey),
  });
  await ensureOk(resp);
  if (!resp.body) throw new SearchError(errorText(0, null));
  for await (const e of readEvents(resp.body)) onEvent(e);
}

export async function searchOnce(query: string, siteKey: string | null): Promise<SearchPayload> {
  const resp = await fetch("/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body(query, siteKey),
  });
  await ensureOk(resp);
  return normalizePayload(await resp.json());
}

// Сколько объектов в базе — для строки под заголовком; любая ошибка → null, строку не показываем.
export async function listingsCount(): Promise<number | null> {
  try {
    const resp = await fetch("/health");
    const n = resp.ok ? (await resp.json())?.listings : null;
    return Number.isInteger(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}
