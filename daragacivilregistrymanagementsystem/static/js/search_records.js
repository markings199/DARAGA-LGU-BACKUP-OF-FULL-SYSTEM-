(function () {
  function submitSearchForm(form) {
    if (!form) return;
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.submit();
  }

  window.crSubmitSearch = function (el) {
    if (!el || !el.value) return;
    submitSearchForm(el.form || document.getElementById("search-criteria-form"));
  };

  if (window.__crSearchLive) return;
  window.__crSearchLive = true;

  document.addEventListener(
    "change",
    function (event) {
      var el = event.target;
      if (!el || el.id !== "search-doctype") return;
      window.crSubmitSearch(el);
    },
    true
  );
})();
