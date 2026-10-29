(function () {
  var cleanup = function () {};

  function registerHasFindQuery() {
    try {
      return Boolean(new URLSearchParams(window.location.search).get("q"));
    } catch (err) {
      return false;
    }
  }

  function bindFind(findInput) {
    var findTimer = null;
    function restoreFullRegister() {
      if (findInput.value.trim()) return;
      if (!registerHasFindQuery()) return;
      var form = findInput.closest("form");
      if (form && typeof form.requestSubmit === "function") {
        form.requestSubmit();
        return;
      }
      if (form) {
        form.submit();
        return;
      }
      var params = new URLSearchParams(window.location.search);
      params.delete("q");
      params.delete("page");
      var query = params.toString();
      window.location.assign(window.location.pathname + (query ? "?" + query : ""));
    }
    function onInput() {
      if (findTimer) window.clearTimeout(findTimer);
      if (findInput.value.trim()) return;
      findTimer = window.setTimeout(restoreFullRegister, 120);
    }
    function onSearch() {
      if (!findInput.value.trim()) restoreFullRegister();
    }
    findInput.addEventListener("input", onInput);
    findInput.addEventListener("search", onSearch);
    return function () {
      if (findTimer) window.clearTimeout(findTimer);
      findInput.removeEventListener("input", onInput);
      findInput.removeEventListener("search", onSearch);
    };
  }

  function bindSelectWheel(selects) {
    function onWheel(event) {
      event.preventDefault();
    }
    selects.forEach(function (el) {
      el.addEventListener("wheel", onWheel, { passive: false });
    });
    return function () {
      selects.forEach(function (el) {
        el.removeEventListener("wheel", onWheel);
      });
    };
  }

  function bindScroller(scroller) {
    if (!scroller) return function () {};
    scroller.setAttribute("tabindex", "0");
    function onWheel(event) {
      if (event.ctrlKey) return;
      var canX = scroller.scrollWidth > scroller.clientWidth + 2;
      var canY = scroller.scrollHeight > scroller.clientHeight + 2;
      if (!canX && !canY) return;
      var dx = event.deltaX || 0;
      var dy = event.deltaY || 0;
      if (event.shiftKey && canX) {
        event.preventDefault();
        scroller.scrollLeft += dy || dx;
        return;
      }
      if (canX && Math.abs(dx) > Math.abs(dy)) {
        event.preventDefault();
        scroller.scrollLeft += dx;
        return;
      }
      if (canY && Math.abs(dy) >= Math.abs(dx)) {
        event.preventDefault();
        scroller.scrollTop += dy;
      }
    }
    scroller.addEventListener("wheel", onWheel, { passive: false });
    return function () {
      scroller.removeEventListener("wheel", onWheel);
    };
  }

  var zoomPct = 100;
  var zoomMode = document.body.classList.contains("reg-is-fitted") ? "fit" : "manual";

  function zoomStage() { return document.querySelector(".reg-zoom-stage"); }
  function zoomForm() { return document.querySelector(".reg-form"); }
  function zoomScroller() { return document.querySelector(".reg-scroll"); }

  function paintZoomButtons() {
    var fitBtn = document.getElementById("reg-zoom-fit");
    var full = document.getElementById("reg-zoom-100");
    var label = document.getElementById("reg-zoom-pct");
    if (fitBtn) fitBtn.classList.toggle("is-zoom-on", zoomMode === "fit");
    if (full) full.classList.toggle("is-zoom-on", zoomMode === "manual" && zoomPct === 100);
    if (label) label.textContent = zoomMode === "fit" ? "Fit" : zoomPct + "%";
    document.body.classList.toggle("reg-is-fitted", zoomMode === "fit");
    document.body.classList.toggle("reg-manual-zoom", zoomMode === "manual");
    var scroller = zoomScroller();
    if (scroller) {
      if (zoomMode === "fit") scroller.removeAttribute("title");
      else scroller.setAttribute("title", "Scroll to see more of the register");
    }
  }

  function clearZoomScale() {
    var form = zoomForm();
    var stage = zoomStage();
    if (form) {
      form.style.zoom = "";
      form.style.transform = "";
      form.style.transformOrigin = "";
    }
    if (stage) {
      stage.style.width = "";
      stage.style.height = "";
    }
  }

  function fittedPercent() {
    var scroller = zoomScroller();
    var form = zoomForm();
    if (!scroller || !form) return 100;
    clearZoomScale();
    var avail = Math.max(120, scroller.clientWidth - 28);
    var need = Math.max(form.scrollWidth, form.offsetWidth);
    if (need < 20) return 100;
    return Math.max(32, Math.min(100, Math.round((avail / need) * 100)));
  }

  function applyZoom(nextPct) {
    zoomMode = "manual";
    zoomPct = Math.max(32, Math.min(200, Math.round(nextPct)));
    var form = zoomForm();
    var stage = zoomStage();
    if (!form) {
      paintZoomButtons();
      return;
    }
    var scale = zoomPct / 100;
    form.style.zoom = "";
    form.style.transformOrigin = "top left";
    form.style.transform = "scale(" + scale + ")";
    var width = form.scrollWidth || form.offsetWidth;
    var height = form.scrollHeight || form.offsetHeight;
    if (stage) {
      stage.style.width = Math.ceil(width * scale) + "px";
      stage.style.height = Math.ceil(height * scale) + "px";
    }
    paintZoomButtons();
  }

  function fitZoom() {
    zoomMode = "fit";
    zoomPct = 100;
    clearZoomScale();
    paintZoomButtons();
    var scroller = zoomScroller();
    if (scroller) scroller.scrollLeft = 0;
  }

  function bumpZoom(delta) {
    var base = zoomMode === "fit" ? fittedPercent() : zoomPct;
    applyZoom(base + delta);
  }

  function bindZoom() {
    paintZoomButtons();
    return function () {};
  }

  if (!window.__crRegZoomBound) {
    window.__crRegZoomBound = true;
    document.addEventListener("click", function (event) {
      var btn = event.target && event.target.closest ? event.target.closest("button") : null;
      if (!btn || !btn.id) return;
      if (btn.id === "reg-zoom-out") {
        event.preventDefault();
        bumpZoom(-10);
      } else if (btn.id === "reg-zoom-in") {
        event.preventDefault();
        bumpZoom(10);
      } else if (btn.id === "reg-zoom-fit") {
        event.preventDefault();
        fitZoom();
      } else if (btn.id === "reg-zoom-100") {
        event.preventDefault();
        applyZoom(100);
      }
    });
    document.addEventListener(
      "wheel",
      function (event) {
        if (!event.ctrlKey) return;
        if (!event.target || !event.target.closest || !event.target.closest(".reg-scroll, .reg-zoom-controls")) return;
        event.preventDefault();
        bumpZoom(event.deltaY > 0 ? -10 : 10);
      },
      { passive: false }
    );
    window.addEventListener("resize", function () {
      if (zoomMode === "fit") fitZoom();
    });
  }

  function initRegisterPage() {
    cleanup();
    var undos = [];
    var findInput = document.getElementById("reg-find-q");
    if (findInput) undos.push(bindFind(findInput));
    undos.push(bindSelectWheel(Array.prototype.slice.call(document.querySelectorAll(".reg-toolbar select, .reg-year-input"))));
    var form = document.querySelector(".reg-form");
    var scroller = document.querySelector(".reg-scroll");
    var stage = document.querySelector(".reg-zoom-stage");
    undos.push(bindScroller(scroller));
    undos.push(bindZoom(form, scroller, stage));
    cleanup = function () {
      undos.forEach(function (fn) {
        try { fn(); } catch (err) {}
      });
      cleanup = function () {};
    };
  }

  initRegisterPage();
  if (!window.__crRegReportsListen) {
    window.__crRegReportsListen = true;
    window.addEventListener("cr:page", initRegisterPage);
  }
})();
