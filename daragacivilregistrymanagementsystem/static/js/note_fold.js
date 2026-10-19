(function () {
  var openDetails = null;
  var openPanel = null;

  function panelOf(details) {
    return details ? details.querySelector(".note-fold-panel") || details._notePanel || null : null;
  }

  function clearInline(panel) {
    if (!panel) return;
    panel.style.position = "";
    panel.style.left = "";
    panel.style.top = "";
    panel.style.width = "";
    panel.style.maxWidth = "";
    panel.style.maxHeight = "";
    panel.style.overflow = "";
    panel.style.margin = "";
    panel.style.zIndex = "";
    panel.style.boxShadow = "";
  }

  function closeOpen() {
    if (openPanel && openDetails) {
      openPanel.classList.remove("is-portal");
      clearInline(openPanel);
      if (openPanel.parentNode !== openDetails) {
        openDetails.appendChild(openPanel);
      }
      openDetails._notePanel = null;
      if (openDetails.open) openDetails.removeAttribute("open");
    }
    openPanel = null;
    openDetails = null;
  }

  function place(panel, summary) {
    var r = summary.getBoundingClientRect();
    var width = Math.min(340, Math.max(240, window.innerWidth - 24));
    var left = Math.min(Math.max(12, r.left), window.innerWidth - width - 12);
    var top = r.bottom + 8;
    panel.style.position = "fixed";
    panel.style.zIndex = "8000";
    panel.style.width = width + "px";
    panel.style.maxWidth = "calc(100vw - 24px)";
    panel.style.maxHeight = "min(40vh, 16rem)";
    panel.style.overflow = "auto";
    panel.style.left = left + "px";
    panel.style.top = top + "px";
    panel.style.margin = "0";
    panel.style.boxShadow = "0 16px 36px rgba(15, 23, 42, 0.22)";
    var pr = panel.getBoundingClientRect();
    if (pr.bottom > window.innerHeight - 10) {
      panel.style.top = Math.max(10, r.top - pr.height - 8) + "px";
    }
    if (pr.right > window.innerWidth - 10) {
      panel.style.left = Math.max(12, window.innerWidth - width - 12) + "px";
    }
  }

  function openFold(details) {
    var panel = panelOf(details);
    var summary = details.querySelector("summary");
    if (!panel || !summary) return;
    if (openDetails && openDetails !== details) closeOpen();
    details._notePanel = panel;
    document.body.appendChild(panel);
    panel.classList.add("is-portal");
    openDetails = details;
    openPanel = panel;
    place(panel, summary);
  }

  document.addEventListener(
    "toggle",
    function (e) {
      var details = e.target;
      if (!details || !details.classList || !details.classList.contains("note-fold")) return;
      if (details.open) openFold(details);
      else if (openDetails === details) closeOpen();
    },
    true
  );

  document.addEventListener("pointerdown", function (e) {
    if (!openDetails) return;
    if (openDetails.contains(e.target)) return;
    if (openPanel && openPanel.contains(e.target)) return;
    closeOpen();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeOpen();
  });

  window.addEventListener(
    "scroll",
    function (e) {
      if (!openDetails) return;
      if (openPanel && (e.target === openPanel || openPanel.contains(e.target))) return;
      closeOpen();
    },
    true
  );
  window.addEventListener("resize", function () {
    if (openDetails) closeOpen();
  });
})();
