(function () {
  var LEAVE_MS = 160;
  var leaving = false;

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function resetEnter() {
    leaving = false;
    document.documentElement.classList.remove("pt-leaving");
  }

  window.addEventListener("pageshow", function (event) {
    if (event.persisted) resetEnter();
  });
  window.addEventListener("pagehide", resetEnter);

  function skipLink(anchor) {
    if (!anchor || anchor.hasAttribute("download")) return true;
    if (anchor.getAttribute("data-no-page-transition") === "1") return true;
    var target = (anchor.getAttribute("target") || "").toLowerCase();
    if (target && target !== "_self") return true;
    var raw = anchor.getAttribute("href") || "";
    if (!raw || raw.charAt(0) === "#") return true;
    var lower = raw.trim().toLowerCase();
    if (lower.indexOf("javascript:") === 0 || lower.indexOf("mailto:") === 0 || lower.indexOf("tel:") === 0) {
      return true;
    }
    var url;
    try {
      url = new URL(anchor.href, window.location.href);
    } catch (e) {
      return true;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return true;
    if (url.origin !== window.location.origin) return true;
    var here = window.location.href.split("#")[0];
    var next = url.href.split("#")[0];
    if (here === next) return true;
    return false;
  }

  function go(href) {
    if (leaving) return;
    leaving = true;
    document.documentElement.classList.add("pt-leaving");
    window.setTimeout(function () {
      window.location.href = href;
    }, LEAVE_MS);
  }

  document.addEventListener("click", function (event) {
    if (reducedMotion()) return;
    if (event.defaultPrevented) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var anchor = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!anchor || skipLink(anchor)) return;
    event.preventDefault();
    go(anchor.href);
  });
})();
