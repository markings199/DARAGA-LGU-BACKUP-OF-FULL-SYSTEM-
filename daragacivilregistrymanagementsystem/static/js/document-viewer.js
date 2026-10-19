(function () {
  function parsePadding(style, side) {
    return parseFloat(style[side]) || 0;
  }

  /** Content width inside the scrollable pane (padding excluded). */
  function getPaneContentWidth(pane) {
    var s = window.getComputedStyle(pane);
    var pl = parsePadding(s, "paddingLeft");
    var pr = parsePadding(s, "paddingRight");
    return Math.max(1, Math.floor(pane.clientWidth - pl - pr));
  }

  /** Content height inside the scrollable pane (padding excluded). */
  function getPaneContentHeight(pane) {
    var s = window.getComputedStyle(pane);
    var pt = parsePadding(s, "paddingTop");
    var pb = parsePadding(s, "paddingBottom");
    return Math.max(1, Math.floor(pane.clientHeight - pt - pb));
  }

  function initSection(section) {
    if (section.getAttribute("data-document-viewer-inited")) return;
    section.setAttribute("data-document-viewer-inited", "1");

    var pfx = section.getAttribute("data-viewer-prefix") || "doc";
    /* Birth, marriage, death, and any other scan: full width, whole page visible. */
    var pane = document.getElementById(pfx + "-viewer-pane");
    var wrap = document.getElementById(pfx + "-image-wrap");
    var img = document.getElementById(pfx + "-viewer-image");
    var valueEl = document.getElementById(pfx + "-zoom-value");
    var zoomOutBtn = document.getElementById(pfx + "-zoom-out");
    var zoomInBtn = document.getElementById(pfx + "-zoom-in");
    var resetBtn = document.getElementById(pfx + "-zoom-reset");
    if (!pane || !wrap || !img) return;

    var scale = 1;
    var minScale = 0.25;
    var maxScale = 4;
    var step = 0.25;
    var drag = null;
    var DRAG_THRESHOLD = 3;

    img.setAttribute("draggable", "false");
    img.style.userSelect = "none";
    img.style.webkitUserDrag = "none";

    function paneIsVisible() {
      if (!pane.getClientRects || !pane.getClientRects().length) return false;
      var host = section.closest("[hidden]");
      if (host && host.hidden) return false;
      return true;
    }

    function applyZoom() {
      if (!paneIsVisible()) {
        if (!section.__dvRetry) {
          section.__dvRetry = window.setTimeout(function () {
            section.__dvRetry = 0;
            applyZoom();
          }, 60);
        }
        return;
      }
      var paneBox = pane.getBoundingClientRect();
      var availW = Math.max(32, getPaneContentWidth(pane) || Math.floor(paneBox.width) || 32);

      var zoomedIn = scale > 1.01;
      var zoomedOut = scale < 0.99;
      pane.style.clipPath = "none";
      pane.style.height = "auto";
      pane.classList.toggle("is-zoomed", zoomedIn);
      pane.classList.toggle("is-shrunk", zoomedOut);

      img.style.objectFit = "contain";
      img.style.objectPosition = "top center";
      img.style.minWidth = "0";
      wrap.style.minWidth = "0";
      wrap.style.transform = "none";
      wrap.style.overflow = "visible";

      if (!zoomedIn && !zoomedOut) {
        pane.style.display = "block";
        pane.style.alignItems = "";
        pane.style.justifyContent = "";
        pane.style.setProperty("overflow", "visible", "important");
        pane.style.setProperty("overflow-x", "visible", "important");
        pane.style.setProperty("overflow-y", "visible", "important");
        pane.style.setProperty("max-height", "none", "important");
        img.style.width = "100%";
        img.style.maxWidth = "100%";
        img.style.height = "auto";
        wrap.style.width = "100%";
        wrap.style.maxWidth = "100%";
        wrap.style.margin = "0";
        wrap.style.height = "auto";
      } else {
        var displayW = Math.max(1, Math.round(availW * scale));
        pane.style.display = "flex";
        pane.style.justifyContent = "center";
        pane.style.alignItems = "flex-start";
        if (zoomedIn) {
          pane.style.overflow = "auto";
          pane.style.overflowX = "auto";
          pane.style.overflowY = "auto";
          pane.style.maxHeight = "min(82vh, 920px)";
        } else {
          pane.style.setProperty("overflow", "visible", "important");
          pane.style.setProperty("overflow-x", "visible", "important");
          pane.style.setProperty("overflow-y", "visible", "important");
          pane.style.setProperty("max-height", "none", "important");
        }
        img.style.width = displayW + "px";
        img.style.maxWidth = "none";
        img.style.height = "auto";
        wrap.style.width = displayW + "px";
        wrap.style.maxWidth = "none";
        wrap.style.margin = "0";
        wrap.style.height = "auto";
      }
      if (valueEl) valueEl.textContent = Math.round(scale * 100) + "%";
      if (zoomOutBtn) zoomOutBtn.disabled = scale <= minScale;
      if (zoomInBtn) zoomInBtn.disabled = scale >= maxScale;
      try {
        section.dispatchEvent(new CustomEvent("document-viewer-layout"));
      } catch (err) {}
    }

    section.__dvReapplyZoom = applyZoom;

    if (zoomInBtn) {
      zoomInBtn.addEventListener("click", function () {
        if (scale < maxScale) {
          scale = Math.min(maxScale, scale + step);
          applyZoom();
        }
      });
    }
    if (zoomOutBtn) {
      zoomOutBtn.addEventListener("click", function () {
        if (scale > minScale) {
          scale = Math.max(minScale, scale - step);
          applyZoom();
        }
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        scale = 1;
        applyZoom();
        pane.scrollTop = 0;
        pane.scrollLeft = 0;
      });
    }

    /*
     * Prefer scrolling the certificate when it still has room to move.
     * At the top/bottom edge (or when nothing overflows), let the wheel
     * pass through so Scan Workflow / .main can scroll normally.
     */
    pane.addEventListener(
      "wheel",
      function (e) {
        var canY = pane.scrollHeight > pane.clientHeight + 1;
        var canX = pane.scrollWidth > pane.clientWidth + 1;
        if (!canY && !canX) return;

        var dx = e.deltaX || 0;
        var dy = e.deltaY || 0;
        if (e.shiftKey && !dx && dy) {
          dx = dy;
          dy = 0;
        }

        var used = false;
        if (canY && Math.abs(dy) >= Math.abs(dx) && dy !== 0) {
          var maxTop = pane.scrollHeight - pane.clientHeight;
          var nextTop = Math.min(maxTop, Math.max(0, pane.scrollTop + dy));
          if (nextTop !== pane.scrollTop) {
            pane.scrollTop = nextTop;
            used = true;
          }
        }
        if (!used && canX && dx !== 0) {
          var maxLeft = pane.scrollWidth - pane.clientWidth;
          var nextLeft = Math.min(maxLeft, Math.max(0, pane.scrollLeft + dx));
          if (nextLeft !== pane.scrollLeft) {
            pane.scrollLeft = nextLeft;
            used = true;
          }
        }

        if (used) {
          e.preventDefault();
          e.stopPropagation();
        }
      },
      { passive: false }
    );

    /* Click-drag to pan — only after a small move so wheel/click stay free. */
    pane.addEventListener("pointerdown", function (e) {
      if (scale <= 1.01) return;
      if (e.button !== 0) return;
      if (e.target.closest && e.target.closest("button, a, input, select, textarea")) return;
      drag = {
        pointerId: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        left: pane.scrollLeft,
        top: pane.scrollTop,
        active: false,
      };
    });
    pane.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.pointerId) return;
      var movedX = e.clientX - drag.x;
      var movedY = e.clientY - drag.y;
      if (!drag.active) {
        if (Math.abs(movedX) < DRAG_THRESHOLD && Math.abs(movedY) < DRAG_THRESHOLD) return;
        drag.active = true;
        pane.classList.add("is-panning");
        try {
          pane.setPointerCapture(e.pointerId);
        } catch (err) {}
      }
      pane.scrollLeft = drag.left - movedX;
      pane.scrollTop = drag.top - movedY;
      e.preventDefault();
    });
    function endDrag(e) {
      if (!drag) return;
      if (e && typeof e.pointerId === "number" && e.pointerId !== drag.pointerId) return;
      drag = null;
      pane.classList.remove("is-panning");
    }
    pane.addEventListener("pointerup", endDrag);
    pane.addEventListener("pointercancel", endDrag);
    pane.addEventListener("pointerleave", function (e) {
      if (drag && !drag.active) endDrag(e);
    });

    function reapplyAfterLayout() {
      requestAnimationFrame(function () {
        requestAnimationFrame(applyZoom);
      });
    }

    img.addEventListener("load", function () {
      scale = 1;
      pane.scrollTop = 0;
      pane.scrollLeft = 0;
      reapplyAfterLayout();
    });
    if (typeof MutationObserver !== "undefined") {
      var srcWatch = new MutationObserver(function () {
        scale = 1;
        pane.scrollTop = 0;
        pane.scrollLeft = 0;
        reapplyAfterLayout();
      });
      srcWatch.observe(img, { attributes: true, attributeFilter: ["src"] });
    }
    if (img.complete && img.naturalWidth) {
      reapplyAfterLayout();
    } else {
      applyZoom();
    }

    if (typeof ResizeObserver !== "undefined") {
      var ro = new ResizeObserver(function () {
        reapplyAfterLayout();
      });
      ro.observe(pane);
      ro.observe(section);
      if (section.parentElement) ro.observe(section.parentElement);
    } else {
      window.addEventListener("resize", reapplyAfterLayout);
    }

    var host = section.parentElement;
    if (host && typeof MutationObserver !== "undefined") {
      var mo = new MutationObserver(function () {
        reapplyAfterLayout();
      });
      mo.observe(host, { attributes: true, attributeFilter: ["hidden", "style", "class"] });
    }
  }

  function initAll() {
    document
      .querySelectorAll(".document-viewer-section[data-viewer-prefix]")
      .forEach(initSection);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
