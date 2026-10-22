/* Reload Archiving periodically so the table stays in sync with the database (no Flask routes). */
(function () {
  var registryInput = document.getElementById("archive-registry");
  if (registryInput) {
    var restoreTimer = null;
    function urlHasRegistryQuery() {
      try {
        return Boolean(new URLSearchParams(window.location.search).get("registry"));
      } catch (err) {
        return false;
      }
    }
    function restoreFullList() {
      if (registryInput.value.trim()) return;
      if (!urlHasRegistryQuery()) return;
      var params = new URLSearchParams(window.location.search);
      params.delete("registry");
      var query = params.toString();
      window.location.assign(window.location.pathname + (query ? "?" + query : ""));
    }
    registryInput.addEventListener("input", function () {
      if (restoreTimer) window.clearTimeout(restoreTimer);
      if (registryInput.value.trim()) return;
      restoreTimer = window.setTimeout(restoreFullList, 80);
    });
    registryInput.addEventListener("search", function () {
      if (!registryInput.value.trim()) restoreFullList();
    });
  }

  if (!document.getElementById("archive-registry") && !document.querySelector(".archiving-hero")) {
    return;
  }
  if (window.__crArchiveTimer) {
    window.clearInterval(window.__crArchiveTimer);
  }
  var ms = 40000;
  window.__crArchiveTimer = window.setInterval(function () {
    if (document.hidden) return;
    if (!document.getElementById("archive-registry") && !document.querySelector(".archiving-hero")) {
      window.clearInterval(window.__crArchiveTimer);
      window.__crArchiveTimer = null;
      return;
    }
    var bar = document.getElementById("ocr-job-bar");
    if (bar && bar.getAttribute("aria-busy") === "true") return;
    window.location.reload();
  }, ms);
})();
