(function () {
  "use strict";

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function setQueryStatus(status) {
    try {
      var url = new URL(window.location.href);
      if (status) url.searchParams.set("status", status);
      else url.searchParams.delete("status");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    } catch (e) {}
  }

  function scopeOf(root) {
    return (
      root.closest(".staff-requests-shell") ||
      root.closest(".admin-section") ||
      root.parentElement ||
      document
    );
  }

  function apply(root, status, animate) {
    var scope = scopeOf(root);
    var table = scope.querySelector("table.staff-requests-table, table.admin-requests-table, table[data-progress-table]");
    var rows = table
      ? Array.prototype.slice.call(table.querySelectorAll("tbody tr[data-progress]"))
      : [];
    var empty = scope.querySelector("[data-staff-filter-empty]");
    var wrap = scope.querySelector("[data-staff-table-wrap], .admin-requests-table-wrap");
    var buttons = Array.prototype.slice.call(root.querySelectorAll(".staff-progress-btn[data-status]"));
    var visible = 0;
    rows.forEach(function (row) {
      var match = !status || row.getAttribute("data-progress") === status;
      row.classList.toggle("is-filtered-out", !match);
      if (match) {
        visible += 1;
        if (animate && !reducedMotion()) {
          row.classList.remove("is-row-in");
          void row.offsetWidth;
          row.classList.add("is-row-in");
        }
      }
    });
    buttons.forEach(function (btn) {
      var active = (btn.getAttribute("data-status") || "") === (status || "");
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (empty) empty.hidden = !(rows.length && visible === 0);
    if (wrap) wrap.hidden = rows.length > 0 && visible === 0;
    var form = root.querySelector("form.request-doc-type-filter");
    var formStatus = form ? form.querySelector('input[name="status"]') : null;
    if (formStatus) {
      if (status) formStatus.value = status;
      else if (formStatus.parentNode) formStatus.parentNode.removeChild(formStatus);
    } else if (status && form) {
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "status";
      hidden.value = status;
      form.appendChild(hidden);
    }
    setQueryStatus(status);
  }

  function applyAll() {
    document.querySelectorAll("[data-staff-progress-filters]").forEach(function (root) {
      apply(root, root.getAttribute("data-initial-status") || "", false);
    });
  }

  document.addEventListener(
    "click",
    function (event) {
      var btn = event.target && event.target.closest ? event.target.closest(".staff-progress-btn") : null;
      if (!btn || !btn.hasAttribute("data-status")) return;
      var root = btn.closest("[data-staff-progress-filters]");
      if (!root) return;
      event.preventDefault();
      event.stopPropagation();
      var status = btn.getAttribute("data-status") || "";
      apply(root, status, true);
      if (root.getAttribute("data-filter-mode") === "navigate") {
        try {
          var url = new URL(window.location.href);
          if (status) url.searchParams.set("status", status);
          else url.searchParams.delete("status");
          url.searchParams.delete("page");
          window.setTimeout(function () {
            window.location.assign(url.pathname + url.search + url.hash);
          }, 80);
        } catch (e) {}
      }
    },
    true
  );

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyAll);
  } else {
    applyAll();
  }
  window.addEventListener("cr:page", applyAll);
})();
