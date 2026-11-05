// Виджет поиска квартир: кнопка + окно с чатом в iframe. Подключение одной строкой:
//   <script src="https://<домен>/widget.js" data-site-key="pub_demo" defer></script>
// Клик по объекту в чате → событие "prop-mind:listing" на window сайта: { detail: { source_id } }.
// Если у сайта своя CSP — разрешить домен виджета в script-src, style-src и frame-src.
(() => {
  const script = document.currentScript;
  if (!script) return;
  const ORIGIN = new URL(script.src).origin;
  const siteKey = script.dataset.siteKey || "";
  const label = script.dataset.label || "Подобрать квартиру";

  // стили — файлом с нашего домена, не инлайн: строгая CSP сайта блокирует вставленный <style>
  const style = document.createElement("link");
  style.rel = "stylesheet";
  style.href = new URL("/static/widget.css", ORIGIN).href;
  document.head.append(style);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pm-btn";
  btn.textContent = label;
  btn.setAttribute("aria-haspopup", "dialog");
  btn.setAttribute("aria-expanded", "false");

  const pop = document.createElement("div");
  pop.className = "pm-pop";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", label);

  const close = document.createElement("button");
  close.type = "button";
  close.className = "pm-close";
  close.setAttribute("aria-label", "Закрыть");
  close.textContent = "×";

  let frame = null; // iframe создаём при первом открытии — не грузим чат, пока не нужен

  function open() {
    if (!frame) {
      frame = document.createElement("iframe");
      frame.title = label;
      const url = new URL("/chat", ORIGIN);
      url.search = new URLSearchParams({ embed: "1", site_key: siteKey, host_origin: location.origin });
      frame.src = url.href;
      pop.prepend(frame);
    }
    pop.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    close.focus();
  }

  function hide() {
    pop.classList.remove("open");
    btn.setAttribute("aria-expanded", "false");
    btn.focus();
  }

  btn.addEventListener("click", () => (pop.classList.contains("open") ? hide() : open()));
  close.addEventListener("click", hide);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pop.classList.contains("open")) hide(); });

  // только сообщения от нашего чата
  window.addEventListener("message", (e) => {
    if (e.origin !== ORIGIN || !e.data || e.data.type !== "prop-mind:listing") return;
    window.dispatchEvent(new CustomEvent("prop-mind:listing", { detail: { source_id: e.data.source_id } }));
  });

  pop.append(close);
  document.body.append(btn, pop);
})();
