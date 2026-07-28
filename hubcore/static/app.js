// Небольшие локальные улучшения интерфейса. Никаких внешних запросов.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const msg = form.getAttribute("data-confirm");
      if (msg && !window.confirm(msg)) {
        e.preventDefault();
      }
    });
  });

  // Счётчик «принято N из M» пересчитывается прямо на странице: без этого
  // он оставался бы прежним до перезагрузки, и отметки выглядели бы так,
  // будто не сохранились.
  function updateDoseSummary() {
    const box = document.querySelector("[data-dose-summary]");
    if (!box) return;
    const taken = document.querySelectorAll(".dose-dot.taken").length;
    const total = document.querySelectorAll(".dose-dot").length;
    box.textContent = `Принято ${taken} из ${total} за месяц.`;
  }

  // Отметка о приёме таблетки: обычная форма с кнопкой-точкой (работает и
  // без JS — просто перезагрузит страницу на тот же месяц). Здесь только
  // ускоряем клик: подкрашиваем точку сразу, без ожидания полной
  // перезагрузки, а сам POST всё равно уходит на сервер в фоне.
  document.querySelectorAll("form.dose-form").forEach((form) => {
    form.addEventListener("submit", (e) => {
      const btn = form.querySelector(".dose-dot");
      if (!btn || typeof fetch !== "function") {
        return; // нет кнопки или fetch недоступен — обычный submit
      }
      e.preventDefault();
      btn.classList.toggle("taken");
      updateDoseSummary();
      const data = new FormData(form);
      fetch(form.action, { method: "POST", body: data })
        .catch(() => {
          btn.classList.toggle("taken"); // откатить оптимистичную отметку
          updateDoseSummary();
          form.submit();
        });
    });
  });
});
