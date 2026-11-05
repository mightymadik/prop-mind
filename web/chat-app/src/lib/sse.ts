import type { SearchEvent } from "../types";
import { normalizeEvent } from "./normalize";

// SSE поверх fetch: EventSource умеет только GET, а /search — POST.

/** Вынимает из буфера готовые события (разделитель — пустая строка); хвост остаётся в rest. */
export function takeEvents(buffer: string): { events: SearchEvent[]; rest: string } {
  const events: SearchEvent[] = [];
  let rest = buffer;
  let i: number;
  while ((i = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, i);
    rest = rest.slice(i + 2);
    let name = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7);
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (!data) continue;
    const event = normalizeEvent(name, JSON.parse(data));
    if (event) events.push(event); // неизвестные события пропускаем
  }
  return { events, rest };
}

/** События из тела ответа по мере прихода кусков. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<SearchEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder(); // stream: true — склеивает символ, разрезанный между кусками
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    const { events, rest } = takeEvents(buffer + decoder.decode(value, { stream: true }));
    buffer = rest;
    yield* events;
  }
}
