// Чат поиска: POST /search со стримингом событий (SSE поверх fetch — EventSource умеет только GET)
// или целиком (?stream=0 — для Cloudflare Quick Tunnel, который SSE не пропускает).
// Текст модели вставляется только текстовыми узлами — никакого innerHTML с ответом.

const params = new URLSearchParams(location.search);
const STREAM = params.get("stream") !== "0";
const EMBED = params.get("embed") === "1";
const SITE_KEY = params.get("site_key") || null;
const HOST_ORIGIN = params.get("host_origin") || null; // сайт с виджетом — для postMessage

const $ = (sel) => document.querySelector(sel);
const ui = {
  form: $(".ask"), input: $("#q"), button: $(".ask button"),
  status: $(".status"), chips: $(".chips"), message: $(".message"),
  answer: $(".answer"), cards: $(".cards"),
};

const STAGES = {
  parse: "Разбираю запрос…",
  search: "Ищу подходящие квартиры…",
  rerank: "Выбираю лучшие…",
  answer: "Пишу ответ…",
};

let results = []; // порядок = номер [n] в тексте ответа

if (EMBED) document.body.classList.add("embed");

function show(el, on = true) { el.hidden = !on; }

function setStatus(text) {
  ui.status.textContent = text || "";
  show(ui.status, Boolean(text));
}

function setMessage(text, isError = false) {
  ui.message.textContent = text || "";
  ui.message.classList.toggle("error", isError);
  show(ui.message, Boolean(text));
}

function reset() {
  results = [];
  ui.chips.replaceChildren();
  ui.answer.replaceChildren();
  ui.cards.replaceChildren(...Array.from({ length: 3 }, () => {
    const li = document.createElement("li");
    li.className = "skeleton";
    li.setAttribute("aria-hidden", "true");
    return li;
  }));
  show(ui.chips, false);
  show(ui.answer, false);
  show(ui.cards, true);
  setMessage("");
}

function renderChips(chips) {
  ui.chips.replaceChildren(...chips.map((c) => {
    const li = document.createElement("li");
    li.textContent = c;
    return li;
  }));
  show(ui.chips, chips.length > 0);
}

const nf = new Intl.NumberFormat("ru-RU");

function renderCards(items) {
  results = items;
  ui.cards.replaceChildren(...items.map((r, i) => {
    const li = document.createElement("li");
    li.className = "card";
    li.id = `card-${r.source_id}`;
    li.tabIndex = 0;
    li.dataset.sourceId = r.source_id;

    const head = document.createElement("div");
    head.className = "head";
    const price = document.createElement("span");
    price.className = "price";
    price.textContent = `${nf.format(r.price_kzt)} ₸`;
    const num = document.createElement("span");
    num.className = "num";
    num.textContent = `№ ${i + 1}`;
    head.append(price, num);

    const facts = document.createElement("p");
    facts.className = "facts";
    facts.textContent = [
      r.rooms ? `${r.rooms} комн.` : null,
      r.area_m2 ? `${r.area_m2} м²` : null,
      r.district ? `${r.district} р-н` : null,
      r.condition_label,
    ].filter(Boolean).join(" · ");

    const tags = document.createElement("ul");
    tags.className = "tags";
    for (const t of r.feature_labels || []) {
      const tag = document.createElement("li");
      tag.textContent = t;
      tags.append(tag);
    }

    const desc = document.createElement("p");
    desc.className = "desc";
    desc.textContent = r.description;

    const more = document.createElement("button");
    more.type = "button";
    more.className = "more";
    more.textContent = "Подробнее";
    more.setAttribute("aria-expanded", "false");
    more.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = li.classList.toggle("open");
      more.textContent = open ? "Свернуть" : "Подробнее";
      more.setAttribute("aria-expanded", String(open));
    });

    li.append(head, facts, tags, desc, more);
    li.addEventListener("click", () => selectListing(r.source_id));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter") selectListing(r.source_id); });
    return li;
  }));
  show(ui.cards, items.length > 0);
}

function selectListing(sourceId) {
  // в виджете сайт сам открывает карточку объекта
  if (EMBED && HOST_ORIGIN && window.parent !== window) {
    window.parent.postMessage({ type: "prop-mind:listing", source_id: sourceId }, HOST_ORIGIN);
  }
}

function highlight(sourceId) {
  const card = document.getElementById(`card-${sourceId}`);
  if (!card) return;
  card.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  card.classList.add("highlight");
  card.focus({ preventScroll: true });
  setTimeout(() => card.classList.remove("highlight"), 2000);
}

// [3] во время стрима (номер), [syn-00012] в финальном тексте (source_id)
const REF = /\[([^\]\s]+)\]/g;

function renderAnswer(text) {
  const nodes = [];
  let last = 0;
  for (const m of text.matchAll(REF)) {
    const token = m[1];
    const byNumber = /^\d+$/.test(token) ? results[Number(token) - 1] : null;
    const byId = results.find((r) => r.source_id === token);
    const target = byNumber || byId;
    if (!target) continue; // не ссылка на объект — оставляем текстом
    nodes.push(document.createTextNode(text.slice(last, m.index)));
    const a = document.createElement("a");
    a.href = `#card-${target.source_id}`;
    a.className = "ref";
    a.textContent = `№ ${results.indexOf(target) + 1}`;
    a.addEventListener("click", (e) => { e.preventDefault(); highlight(target.source_id); });
    nodes.push(a);
    last = m.index + m[0].length;
  }
  nodes.push(document.createTextNode(text.slice(last)));
  ui.answer.replaceChildren(...nodes);
  show(ui.answer, text.length > 0);
}

function errorText(status, detail) {
  if (status === 429 || status === 403 || status === 503) return detail || "Сервис временно недоступен.";
  return "Не получилось выполнить поиск. Попробуйте ещё раз.";
}

async function failed(resp) {
  let detail = null;
  try { detail = (await resp.json()).detail; } catch { /* не JSON */ }
  return errorText(resp.status, typeof detail === "string" ? detail : null);
}

function onEvent(name, data, state) {
  switch (name) {
    case "status": setStatus(STAGES[data.stage] || "Работаю…"); break;
    case "filters": renderChips(data.chips || []); break;
    case "results": renderCards(data.results); break;
    case "delta": state.text += data.text; renderAnswer(state.text); break;
    case "answer": renderAnswer(data.text); break;
    case "clarification":
      ui.cards.replaceChildren(); show(ui.cards, false);
      setMessage(data.text);
      break;
    case "error": setMessage(errorText(502, null), true); break;
    case "done": setStatus(""); break;
  }
}

async function searchStream(query) {
  const resp = await fetch("/search", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({ query, site_key: SITE_KEY }),
  });
  if (!resp.ok) throw new Error(await failed(resp));
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  const state = { text: "" };
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let name = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) name = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent(name, JSON.parse(data), state);
    }
  }
}

async function searchOnce(query) {
  setStatus("Ищу… ответ придёт целиком через несколько секунд");
  const resp = await fetch("/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, site_key: SITE_KEY }),
  });
  if (!resp.ok) throw new Error(await failed(resp));
  const r = await resp.json();
  renderChips(r.chips || []);
  if (r.clarification) {
    ui.cards.replaceChildren(); show(ui.cards, false);
    setMessage(r.clarification);
  } else {
    renderCards(r.results || []);
    renderAnswer(r.answer || "");
  }
  setStatus("");
}

ui.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = ui.input.value.trim();
  if (!query) return;
  reset();
  setStatus(STAGES.parse);
  ui.button.disabled = true;
  try {
    await (STREAM ? searchStream(query) : searchOnce(query));
  } catch (err) {
    ui.cards.replaceChildren(); show(ui.cards, false);
    setStatus("");
    setMessage(err.message || errorText(0, null), true);
  } finally {
    ui.button.disabled = false;
  }
});

// поле растёт по высоте вместе с текстом (до max-height в CSS)
function fitInput() {
  ui.input.style.height = "auto";
  ui.input.style.height = `${ui.input.scrollHeight + 2}px`;
}
ui.input.addEventListener("input", fitInput);

// Enter — отправить, Shift+Enter — новая строка
ui.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ui.form.requestSubmit(); }
});
