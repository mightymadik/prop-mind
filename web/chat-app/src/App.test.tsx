import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { EXAMPLES } from "./components/EmptyState";
import { readParams } from "./params";
import type { Listing } from "./types";

const listing = (id: string, price: number): Listing => ({
  source_id: id, price_kzt: price, rooms: 2, area_m2: 55, district: "Медеуский", condition: "renovated",
  condition_label: "с ремонтом", features: ["parking"], feature_labels: ["паркинг"], description: `Тихий двор ${price}`,
});
const RESULTS = [listing("syn-1", 40_000_000), listing("syn-2", 45_000_000)];

type Ev = [string, unknown];
function sse(events: Ev[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const [name, data] of events) c.enqueue(enc.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`));
      c.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}
const FULL: Ev[] = [
  ["status", { stage: "parse" }],
  ["filters", { filters: {}, chips: ["Алматы", "2 комн."] }],
  ["status", { stage: "search" }],
  ["results", { relaxed: [], results: RESULTS }],
  ["delta", { text: "Лучше [2], " }],
  ["delta", { text: "затем [1]." }],
  ["answer", { text: "Лучше [syn-2], затем [syn-1].", invalid_refs: [], cited: [] }],
  ["done", {}],
];

// calls — только запросы поиска; /health (число объектов на пустом экране) отвечает отдельно
function mockFetch(handler: (init: RequestInit) => Response, health: Response = Response.json({ listings: 1048 })) {
  const calls: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    if (url === "/health") return health;
    calls.push(init);
    return handler(init);
  }));
  return calls;
}

const input = () => screen.getByLabelText("Запрос") as HTMLTextAreaElement;

async function ask(text: string) {
  fireEvent.change(input(), { target: { value: text } });
  await act(async () => {
    fireEvent.keyDown(input(), { key: "Enter" });
  });
}

const turns = () => screen.queryAllByRole("region", { name: /^Запрос:/ });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test("empty screen: greeting and examples; clicking an example sends it", async () => {
  const calls = mockFetch(() => sse(FULL));
  render(<App params={readParams("")} />);
  expect(screen.getByRole("heading", { name: "Найдём квартиру по описанию" })).toBeTruthy();
  expect(within(screen.getByRole("list", { name: "Примеры запросов" })).getAllByRole("button")).toHaveLength(EXAMPLES.length);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: `${EXAMPLES[0].kind}: ${EXAMPLES[0].query}` }));
  });
  expect(JSON.parse(calls[0].body as string).query).toBe(EXAMPLES[0].query);
  expect(screen.queryByRole("heading", { name: "Найдём квартиру по описанию" })).toBeNull();
  expect(within(turns()[0]).getByText(EXAMPLES[0].query).closest(".msg")!.classList.contains("user")).toBe(true);
});

test("empty screen: example cards are labelled by type; the stats line shows N from the server", async () => {
  mockFetch(() => sse(FULL));
  render(<App params={readParams("")} />);
  const examples = within(screen.getByRole("list", { name: "Примеры запросов" })).getAllByRole("button");
  expect(examples.map((b) => b.textContent)).toEqual(EXAMPLES.map((e) => e.kind + e.query));
  expect(await screen.findByText("1 048 объектов · Алматы и Астана · демо-данные")).toBeTruthy();
});

test("no stats line when /health fails", async () => {
  mockFetch(() => sse(FULL), new Response("down", { status: 503 }));
  render(<App params={readParams("")} />);
  await act(async () => {});
  expect(screen.queryByText(/демо-данные/)).toBeNull();
});

test("stream: user bubble, chips, cards with badges, streamed answer with links that highlight the card", async () => {
  const calls = mockFetch(() => sse(FULL));
  render(<App params={readParams("?site_key=pub_demo")} />);
  await ask("двушка в Алматы");

  expect(calls[0].headers).toMatchObject({ accept: "text/event-stream" });
  expect(JSON.parse(calls[0].body as string)).toEqual({ query: "двушка в Алматы", site_key: "pub_demo" });
  const turn = turns()[0];
  expect(within(turn).getByText("Алматы")).toBeTruthy();
  const cards = turn.querySelectorAll(".listing");
  expect(cards).toHaveLength(2);
  expect(within(cards[0] as HTMLElement).getByText("с ремонтом").dataset.variant).toBe("outline"); // состояние — акцентный бейдж
  expect(within(cards[0] as HTMLElement).getByText("паркинг").dataset.variant).toBe("secondary");
  const links = within(turn).getAllByRole("link");
  expect(links.map((a) => a.textContent)).toEqual(["№ 2", "№ 1"]);
  expect(turn.querySelector(".answer-text")!.textContent).not.toMatch(/syn-/);
  expect(within(turn).getByText(/Подборка · 2 варианта/)).toBeTruthy();

  vi.useFakeTimers();
  fireEvent.click(links[0]);
  const card = turn.querySelector('[data-source-id="syn-2"]')!;
  expect(card.classList.contains("highlight")).toBe(true);
  expect(document.activeElement).toBe(card);
  act(() => vi.advanceTimersByTime(2100));
  expect(card.classList.contains("highlight")).toBe(false);
});

test("history: two independent turns; a link highlights the card of its own turn", async () => {
  const calls = mockFetch(() => sse(FULL));
  render(<App params={readParams("")} />);
  await ask("первый");
  await ask("второй");
  expect(turns()).toHaveLength(2);
  expect(calls.map((c) => JSON.parse(c.body as string))).toEqual([
    { query: "первый", site_key: null },
    { query: "второй", site_key: null }, // без истории: каждый запрос независимый
  ]);
  const [first, second] = turns();
  fireEvent.click(within(second).getAllByRole("link")[0]);
  expect(second.querySelector('[data-source-id="syn-2"]')!.classList.contains("highlight")).toBe(true);
  expect(first.querySelector('[data-source-id="syn-2"]')!.classList.contains("highlight")).toBe(false);
});

test("Enter sends, Shift+Enter does not; empty input does not send", async () => {
  const calls = mockFetch(() => sse(FULL));
  render(<App params={readParams("")} />);
  fireEvent.change(input(), { target: { value: "строка" } });
  fireEvent.keyDown(input(), { key: "Enter", shiftKey: true });
  expect(calls).toHaveLength(0);
  fireEvent.change(input(), { target: { value: "   " } });
  await act(async () => {
    fireEvent.keyDown(input(), { key: "Enter" });
  });
  expect(calls).toHaveLength(0);
  await ask("двушка");
  expect(calls).toHaveLength(1);
  expect(input().value).toBe(""); // поле очищается после отправки
});

test("search button: disabled when empty, 'Ищу…' while busy; counter appears near the limit", async () => {
  let close: () => void = () => {};
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/health" ? Response.json({ listings: 1 }) : new Response(new ReadableStream<Uint8Array>({
    start(c) {
      close = () => c.close();
    },
  }), { headers: { "content-type": "text/event-stream" } })));
  render(<App params={readParams("")} />);
  const btn = () => screen.getByRole("button", { name: /Найти|Ищу/ }) as HTMLButtonElement;
  expect(btn().disabled).toBe(true);
  fireEvent.change(input(), { target: { value: "x".repeat(399) } });
  expect(btn().disabled).toBe(false);
  expect(screen.queryByText(/\/ 500/)).toBeNull();
  fireEvent.change(input(), { target: { value: "x".repeat(420) } });
  expect(screen.getByText("420 / 500")).toBeTruthy();
  fireEvent.keyDown(input(), { key: "Enter" });
  await act(async () => {});
  expect(btn().textContent!.trim()).toBe("Ищу…");
  expect(btn().disabled).toBe(true);
  await act(async () => close());
  expect(btn().textContent!.trim()).toBe("Найти");
});

test("clarification is shown as the assistant's question", async () => {
  mockFetch(() => sse([["filters", { filters: {}, chips: ["Астана"] }], ["clarification", { text: "Метро есть только в Алматы. Искать там?" }], ["done", {}]]));
  render(<App params={readParams("")} />);
  await ask("у метро в Астане");
  const msg = turns()[0].querySelector(".msg.assistant")!;
  expect(within(msg as HTMLElement).getByRole("status").textContent).toBe("Метро есть только в Алматы. Искать там?");
  expect(msg.querySelector(".listing")).toBeNull();
});

test("own query scrolls to the bottom even if the user scrolled up", async () => {
  mockFetch(() => sse(FULL));
  render(<App params={readParams("")} />);
  const log = screen.getByRole("log");
  let scrollTop = 0;
  Object.defineProperty(log, "scrollHeight", { configurable: true, get: () => 2000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, get: () => 500 });
  Object.defineProperty(log, "scrollTop", { configurable: true, get: () => scrollTop, set: (v) => (scrollTop = v) });

  await ask("первый");
  expect(scrollTop).toBe(2000);
  scrollTop = 100; // отмотал вверх
  fireEvent.scroll(log);
  await ask("второй"); // свой новый запрос — показываем его, а не кнопку
  expect(scrollTop).toBe(2000);
  expect(screen.queryByRole("button", { name: /К новым/ })).toBeNull();
});

test("'К новым' shows when new content arrives while scrolled up", async () => {
  let push: (s: string) => void = () => {};
  let close: () => void = () => {};
  const enc = new TextEncoder();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
    start(c) {
      push = (s) => c.enqueue(enc.encode(s));
      close = () => c.close();
    },
  }), { headers: { "content-type": "text/event-stream" } })));
  render(<App params={readParams("")} />);
  const log = screen.getByRole("log");
  let scrollTop = 0;
  Object.defineProperty(log, "scrollHeight", { configurable: true, get: () => 2000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, get: () => 500 });
  Object.defineProperty(log, "scrollTop", { configurable: true, get: () => scrollTop, set: (v) => (scrollTop = v) });

  fireEvent.change(input(), { target: { value: "двушка" } });
  fireEvent.keyDown(input(), { key: "Enter" });
  await act(async () => push('event: delta\ndata: {"text":"Начало"}\n\n'));
  expect(scrollTop).toBe(2000);

  scrollTop = 200; // отмотал вверх
  fireEvent.scroll(log);
  await act(async () => push('event: delta\ndata: {"text":" продолжение"}\n\n'));
  expect(scrollTop).toBe(200); // ленту из-под пользователя не дёргаем
  const btn = screen.getByRole("button", { name: /К новым/ });

  fireEvent.click(btn);
  expect(scrollTop).toBe(2000);
  expect(screen.queryByRole("button", { name: /К новым/ })).toBeNull();
  await act(async () => {
    push('event: done\ndata: {}\n\n');
    close();
  });
});

test("a scroll event without upward movement (content grew below) keeps autoscroll", async () => {
  let push: (s: string) => void = () => {};
  const enc = new TextEncoder();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
    start(c) {
      push = (s) => c.enqueue(enc.encode(s));
    },
  }), { headers: { "content-type": "text/event-stream" } })));
  render(<App params={readParams("")} />);
  const log = screen.getByRole("log");
  let scrollTop = 0;
  let height = 2000;
  Object.defineProperty(log, "scrollHeight", { configurable: true, get: () => height });
  Object.defineProperty(log, "clientHeight", { configurable: true, get: () => 500 });
  Object.defineProperty(log, "scrollTop", { configurable: true, get: () => scrollTop, set: (v) => (scrollTop = Math.min(v, height - 500)) });

  fireEvent.change(input(), { target: { value: "двушка" } });
  fireEvent.keyDown(input(), { key: "Enter" });
  await act(async () => push('event: delta\ndata: {"text":"Начало"}\n\n'));
  fireEvent.scroll(log); // событие от нашей же докрутки
  height = 3000; // карточки выросли ниже, scrollTop прежний — браузер шлёт scroll
  fireEvent.scroll(log);
  await act(async () => push('event: delta\ndata: {"text":" ещё"}\n\n'));
  expect(scrollTop).toBe(2500); // докрутили до нового низа
  expect(screen.queryByRole("button", { name: /К новым/ })).toBeNull();
});

test("?stream=0 uses the JSON response", async () => {
  const calls = mockFetch(() => Response.json({ chips: ["Астана"], results: RESULTS, answer: "Подходит [syn-1]" }));
  render(<App params={readParams("?stream=0")} />);
  await ask("однушка в Астане");
  expect(calls[0].headers).not.toHaveProperty("accept");
  expect(screen.getByText("Астана")).toBeTruthy();
  expect(screen.getByRole("link").textContent).toBe("№ 1");
});

test("limit error from the server is shown as the assistant's message", async () => {
  mockFetch(() => Response.json({ detail: "Лимит демо на сегодня исчерпан — попробуйте завтра." }, { status: 429 }));
  render(<App params={readParams("")} />);
  await ask("двушка");
  expect(screen.getByRole("alert").textContent).toBe("Лимит демо на сегодня исчерпан — попробуйте завтра.");
});

test("embed: no header; card click posts the listing to the host origin only", async () => {
  mockFetch(() => Response.json({ results: RESULTS, answer: "" }));
  const post = vi.fn();
  vi.spyOn(window, "parent", "get").mockReturnValue({ postMessage: post } as unknown as Window);

  const { unmount, container } = render(<App params={readParams("?stream=0&embed=1&host_origin=https://agency.kz")} />);
  expect(container.querySelector(".topbar")).toBeNull();
  await ask("двушка");
  fireEvent.click(container.querySelector('[data-source-id="syn-1"]')!);
  expect(post).toHaveBeenCalledWith({ type: "prop-mind:listing", source_id: "syn-1" }, "https://agency.kz");
  unmount();

  post.mockClear();
  const second = render(<App params={readParams("?stream=0&embed=1")} />); // без host_origin — не шлём
  await ask("двушка");
  fireEvent.click(second.container.querySelector('[data-source-id="syn-1"]')!);
  expect(post).not.toHaveBeenCalled();
});

test("older API without chips/labels/status does not blank the page", async () => {
  const old = RESULTS.map(({ feature_labels: _f, condition_label: _c, ...rest }) => rest);
  mockFetch(() => sse([["filters", { filters: { city: "Алматы" } }], ["results", { results: old }], ["answer", { text: "Подходит [syn-1]" }], ["done", {}]]));
  render(<App params={readParams("")} />);
  await ask("двушка");
  expect(document.querySelectorAll(".listing")).toHaveLength(2);
  expect(screen.getByRole("link").textContent).toBe("№ 1");
  expect(input()).toBeTruthy();
});

test("render error shows a message instead of a blank page", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  function Boom(): never {
    throw new Error("boom");
  }
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>,
  );
  expect(screen.getByRole("alert").textContent).toMatch(/Что-то пошло не так/);
});
