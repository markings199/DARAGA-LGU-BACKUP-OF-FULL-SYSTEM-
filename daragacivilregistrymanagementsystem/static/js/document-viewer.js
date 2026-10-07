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
    var fitMode = (section.getAttribute("data-fit") || "width").toLowerCase();
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

    function applyZoom() {
      var baseW = getPaneContentWidth(pane);
      var displayW;
      var displayH = null;
      if (fitMode === "contain") {
        /* Fit the full certificate inside the pane at 100% — no scrolling needed. */
        var baseH = getPaneContentHeight(pane);
        var nw = img.naturalWidth || 0;
        var nh = img.naturalHeight || 0;
        /* Leave a tiny inset so borders never clip the page edge. */
        var availW = Math.max(1, baseW - 2);
        var availH = Math.max(1, baseH - 2);
        if (nw > 0 && nh > 0 && availH > 1) {
          var fit = Math.min(availW / nw, availH / nh);
          if (!isFinite(fit) || fit <= 0) fit = availW / nw;
          displayW = Math.max(1, Math.round(nw * fit * scale));
          displayH = Math.max(1, Math.round(nh * fit * scale));
        } else {
          displayW = Math.max(1, Math.round(availW * scale));
        }
        pane.style.display = "flex";
        pane.style.alignItems = scale <= 1.01 ? "center" : "flex-start";
        pane.style.justifyContent = "center";
        pane.style.overflow = scale <= 1.01 ? "hidden" : "auto";
        pane.style.overflowX = scale <= 1.01 ? "hidden" : "auto";
        pane.style.overflowY = scale <= 1.01 ? "hidden" : "auto";
      } else if (fitMode === "fill") {
        /* Full width of the panel; panel grows with the image so the whole page is visible. */
        displayW = Math.max(1, Math.round(baseW * scale));
        pane.style.display = "block";
        pane.style.alignItems = "";
        pane.style.justifyContent = "";
        if (scale <= 1.01) {
          pane.style.overflow = "visible";
          pane.style.overflowX = "visible";
          pane.style.overflowY = "visible";
          pane.style.height = "auto";
          pane.style.maxHeight = "none";
          pane.style.minHeight = "0";
        } else {
          pane.style.overflow = "auto";
          pane.style.overflowX = "auto";
          pane.style.overflowY = "auto";
          pane.style.height = "";
          pane.style.maxHeight = "min(72vh, 820px)";
        }
      } else {
        /* Width-fill inside a fixed pane (scan workflow / default). */
        displayW = Math.max(1, Math.round(baseW * scale));
        pane.style.display = "";
        pane.style.alignItems = "";
        pane.style.justifyContent = "";
        pane.style.overflow = "auto";
        pane.style.overflowX = "auto";
        pane.style.overflowY = "auto";
      }
      img.style.width = displayW + "px";
      img.style.maxWidth = "none";
      img.style.minWidth = "0";
      if (displayH != null) {
        img.style.height = displayH + "px";
      } else {
        img.style.height = "auto";
      }
      wrap.style.width = displayW + "px";
      wrap.style.maxWidth = "none";
      wrap.style.minWidth = displayW + "px";
      wrap.style.height = displayH != null ? displayH + "px" : "auto";
      wrap.style.transform = "none";
      wrap.style.marginLeft = "0";
      wrap.style.marginRight = "0";
      pane.classList.toggle("is-zoomed", scale > 1.01);
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

    img.addEventListener("load", reapplyAfterLayout);
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
      if (section.parentElement) ro.observe(section.parentElement);
    } else {
      window.addEventListener("resize", reapplyAfterLayout);
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
