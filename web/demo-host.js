// Демо-сайт: так сайт компании ловит выбор объекта в виджете и открывает свою карточку.
window.addEventListener("prop-mind:listing", (e) => {
  document.getElementById("picked").textContent = `Сайт открыл бы карточку объекта ${e.detail.source_id}`;
});
