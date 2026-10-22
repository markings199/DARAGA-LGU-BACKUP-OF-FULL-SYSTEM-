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
      if (!canX) return;
      var goingSideways = event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY);
      if (!goingSideways) return;
      event.preventDefault();
      scroller.scrollLeft += event.deltaX || event.deltaY;
    }
    scroller.addEventListener("wheel", onWheel, { passive: false });
    return function () {
      scroller.removeEventListener("wheel", onWheel);
    };
  }

  function bindZoom(form, scroller, stage) {
    if (!form || !scroller || !document.body.classList.contains("reg-focus")) {
      return function () {};
    }
    var label = document.getElementById("reg-zoom-pct");
    var fitBtn = document.getElementById("reg-zoom-fit");
    var full = document.getElementById("reg-zoom-100");
    var useZoom = typeof CSS !== "undefined" && CSS.supports && CSS.supports("zoom", "0.5");
    var pct = 100;
    var mode = "fit";

    function paintButtons() {
      if (fitBtn) fitBtn.classList.toggle("is-zoom-on", mode === "fit");
      if (full) full.classList.toggle("is-zoom-on", mode === "manual" && pct === 100);
      document.body.classList.toggle("reg-is-fitted", mode === "fit");
      if (scroller) {
        if (mode === "fit") scroller.removeAttribute("title");
        else scroller.setAttribute("title", "Scroll right to see more columns");
      }
    }

    function clearScale() {
      form.style.zoom = "";
      form.style.transform = "";
      if (stage) {
        stage.style.width = "";
        stage.style.height = "";
      }
    }

    function apply(next) {
      pct = Math.max(32, Math.min(160, Math.round(next)));
      var scale = pct / 100;
      if (useZoom) {
        form.style.zoom = String(scale);
        form.style.transform = "";
        if (stage) {
          stage.style.width = "";
          stage.style.height = "";
        }
      } else {
        form.style.zoom = "";
        var width = form.scrollWidth;
        var height = form.offsetHeight;
        form.style.transformOrigin = "top left";
        form.style.transform = "scale(" + scale + ")";
        if (stage) {
          stage.style.width = Math.ceil(width * scale) + "px";
          stage.style.height = Math.ceil(height * scale) + "px";
        }
      }
      if (label) label.textContent = mode === "fit" ? "Fit" : pct + "%";
      paintButtons();
      scroller.scrollLeft = 0;
    }

    function fitWidth() {
      mode = "fit";
      pct = 100;
      clearScale();
      if (label) label.textContent = "Fit";
      paintButtons();
      scroller.scrollLeft = 0;
    }

    function bump(delta) {
      mode = "manual";
      apply(pct + delta);
    }

    var out = document.getElementById("reg-zoom-out");
    var inn = document.getElementById("reg-zoom-in");
    function onOut() { bump(-10); }
    function onIn() { bump(10); }
    function onFit() { fitWidth(); }
    function onFull() {
      mode = "manual";
      apply(100);
    }
    function onCtrlWheel(event) {
      if (!event.ctrlKey) return;
      event.preventDefault();
      bump(event.deltaY > 0 ? -10 : 10);
    }
    function onResize() {
      if (mode === "fit") fitWidth();
    }
    function onBeforePrint() { apply(100); }
    function onAfterPrint() {
      if (mode === "fit") fitWidth();
    }

    if (out) out.addEventListener("click", onOut);
    if (inn) inn.addEventListener("click", onIn);
    if (fitBtn) fitBtn.addEventListener("click", onFit);
    if (full) full.addEventListener("click", onFull);
    scroller.addEventListener("wheel", onCtrlWheel, { passive: false });
    window.addEventListener("resize", onResize);
    window.addEventListener("beforeprint", onBeforePrint);
    window.addEventListener("afterprint", onAfterPrint);
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(fitWidth);
    });

    return function () {
      if (out) out.removeEventListener("click", onOut);
      if (inn) inn.removeEventListener("click", onIn);
      if (fitBtn) fitBtn.removeEventListener("click", onFit);
      if (full) full.removeEventListener("click", onFull);
      scroller.removeEventListener("wheel", onCtrlWheel);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("beforeprint", onBeforePrint);
      window.removeEventListener("afterprint", onAfterPrint);
    };
  }

  function initRegisterPage() {
    cleanup();
    var undos = [];
    var findInput = document.getElementById("reg-find-q");
    if (findInput) undos.push(bindFind(findInput));
    undos.push(bindSelectWheel(Array.prototype.slice.call(document.querySelectorAll(".reg-toolbar select"))));
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
