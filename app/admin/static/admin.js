// IntizomAI admin — kichik yordamchilar (CSP: faqat shu fayl, inline skript yo'q).
(function () {
  // Xavfli amallar: <form data-confirm="..."> yoki <button data-confirm="...">
  document.addEventListener("submit", function (e) {
    var f = e.target;
    var btn = e.submitter;
    var msg = (btn && btn.getAttribute("data-confirm")) || f.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      e.preventDefault();
      return;
    }
    // Ikki marta yuborilmasin.
    if (btn && !btn.hasAttribute("data-keep")) {
      setTimeout(function () { btn.disabled = true; }, 0);
    }
  });

  // Filtr <select data-autosubmit> o'zgarsa — forma yuboriladi.
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-autosubmit]")) e.target.form.submit();
  });

  // Nusxa olish: <button data-copy="matn">
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    navigator.clipboard.writeText(b.getAttribute("data-copy")).then(function () {
      var t = b.textContent;
      b.textContent = "Nusxa olindi ✓";
      setTimeout(function () { b.textContent = t; }, 1400);
    });
  });

  // Mobil menyu
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-burger]")) document.body.classList.toggle("nav-open");
    else if (document.body.classList.contains("nav-open") && !e.target.closest(".side")) document.body.classList.remove("nav-open");
  });

  // Promokod formasi: turga qarab maydonlarni ko'rsatish
  var kind = document.querySelector("[data-promo-kind]");
  if (kind) {
    var sync = function () {
      var k = (document.querySelector("[data-promo-kind]:checked") || {}).value;
      document.querySelectorAll("[data-show-for]").forEach(function (el) {
        el.hidden = el.getAttribute("data-show-for").split(" ").indexOf(k) < 0;
      });
    };
    document.querySelectorAll("[data-promo-kind]").forEach(function (r) { r.addEventListener("change", sync); });
    var count = document.querySelector("[name=count]");
    var syncCount = function () {
      var many = count && parseInt(count.value || "1", 10) > 1;
      document.querySelectorAll("[data-single]").forEach(function (el) { el.hidden = many; });
      document.querySelectorAll("[data-batch]").forEach(function (el) { el.hidden = !many; });
    };
    if (count) count.addEventListener("input", syncCount);
    sync();
    syncCount();
  }

  // E'lon formasi: jonli ko'rinish
  var at = document.querySelector("[data-ann-title]");
  var ab = document.querySelector("[data-ann-body]");
  if (at && ab) {
    var pt = document.querySelector("[data-prev-title]");
    var pb = document.querySelector("[data-prev-body]");
    var upd = function () { pt.textContent = at.value || "Sarlavha"; pb.textContent = ab.value || "Matn"; };
    at.addEventListener("input", upd);
    ab.addEventListener("input", upd);
    upd();
  }
})();
