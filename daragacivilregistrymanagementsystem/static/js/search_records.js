(function () {
  if (window.__crSearchLive) return;
  window.__crSearchLive = true;

  function submitSearchForm(form) {
    if (!form) return;
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.submit();
  }

  document.addEventListener("change", function (event) {
    var el = event.target;
    if (!el || el.id !== "search-doctype") return;
    if (!el.value) return;
    submitSearchForm(el.form || document.getElementById("search-criteria-form"));
  });
})();
