(function () {
  "use strict";

  var page = document.getElementById("cr-page");
  if (!page) return;

  var scriptsBox = document.getElementById("cr-page-scripts");
  var busy = false;
  var gen = 0;
  var abortCtrl = null;
  var progressWrap = null;
  var progressBar = null;
  var progressHideTimer = 0;

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function sameOrigin(href) {
    try {
      return new URL(href, location.href).origin === location.origin;
    } catch (e) {
      return false;
    }
  }

  function ensureProgress() {
    if (progressWrap) return;
    progressWrap = document.createElement("div");
    progressWrap.id = "cr-progress";
    progressWrap.setAttribute("aria-hidden", "true");
    progressBar = document.createElement("span");
    progressBar.id = "cr-progress-bar";
    progressWrap.appendChild(progressBar);
    document.body.appendChild(progressWrap);
  }

  function startProgress() {
    ensureProgress();
    window.clearTimeout(progressHideTimer);
    progressWrap.classList.remove("is-done");
    progressWrap.classList.add("is-on");
    progressBar.style.width = "12%";
    window.requestAnimationFrame(function () {
      progressBar.style.width = "68%";
    });
  }

  function finishProgress() {
    if (!progressWrap) return;
    progressWrap.classList.add("is-done");
    progressBar.style.width = "100%";
    progressHideTimer = window.setTimeout(function () {
      progressWrap.classList.remove("is-on", "is-done");
      progressBar.style.width = "0";
    }, 220);
  }

  function isSafeUrl(url) {
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    if (url.origin !== location.origin) return false;
    if (/\.(pdf|png|jpe?g|gif|webp|zip|xlsx|csv|docx?)(\?|$)/i.test(url.pathname)) return false;
    if (/\/logout\b/i.test(url.pathname)) return false;
    if (/\/login\b/i.test(url.pathname)) return false;
    return true;
  }

  function shouldInterceptAnchor(anchor) {
    if (!anchor || !anchor.getAttribute("href")) return false;
    if (anchor.hasAttribute("data-no-page-transition") || anchor.getAttribute("data-logout")) return false;
    if (anchor.hasAttribute("download")) return false;
    var target = (anchor.getAttribute("target") || "").toLowerCase();
    if (target && target !== "_self") return false;
    var href = anchor.getAttribute("href");
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0 || href.indexOf("mailto:") === 0) {
      return false;
    }
    if (!sameOrigin(anchor.href)) return false;
    var next = new URL(anchor.href, location.href);
    if (!isSafeUrl(next)) return false;
    if (next.pathname === location.pathname && next.search === location.search && next.hash) return false;
    return true;
  }

  function markNav(anchor) {
    var nav = document.querySelector(".sidebar-nav");
    if (!nav || !anchor || !nav.contains(anchor)) return;
    nav.querySelectorAll("a").forEach(function (link) {
      link.classList.toggle("active", link === anchor);
    });
  }

  function headNodeKey(node) {
    if (!node || node.nodeType !== 1) return "";
    if (node.tagName === "LINK") return "link:" + (node.getAttribute("href") || "");
    if (node.tagName === "STYLE") return "style:" + String(node.textContent || "").replace(/\s+/g, " ").slice(0, 120);
    return "";
  }

  function replaceHeadSlice(startName, endName, doc) {
    var start = document.querySelector('meta[name="' + startName + '"]');
    var end = document.querySelector('meta[name="' + endName + '"]');
    var srcStart = doc.querySelector('meta[name="' + startName + '"]');
    var srcEnd = doc.querySelector('meta[name="' + endName + '"]');
    if (!start || !end || !srcStart || !srcEnd) return;
    var live = [];
    while (start.nextSibling && start.nextSibling !== end) {
      live.push(start.nextSibling);
      start.parentNode.removeChild(start.nextSibling);
    }
    var reuse = {};
    live.forEach(function (node) {
      var key = headNodeKey(node);
      if (key) reuse[key] = node;
    });
    var node = srcStart.nextSibling;
    while (node && node !== srcEnd) {
      var next = node.nextSibling;
      if (node.nodeType === 3 && !String(node.textContent || "").trim()) {
        node = next;
        continue;
      }
      var key = headNodeKey(node);
      if (key && reuse[key]) {
        end.parentNode.insertBefore(reuse[key], end);
        delete reuse[key];
      } else if (node.nodeType === 1 && (node.tagName === "STYLE" || node.tagName === "LINK")) {
        var fresh = document.createElement(node.tagName.toLowerCase());
        Array.prototype.slice.call(node.attributes).forEach(function (attr) {
          fresh.setAttribute(attr.name, attr.value);
        });
        if (node.tagName === "STYLE") fresh.textContent = node.textContent;
        end.parentNode.insertBefore(fresh, end);
      } else {
        end.parentNode.insertBefore(document.importNode(node, true), end);
      }
      node = next;
    }
  }

  function replaceExtraHead(doc) {
    replaceHeadSlice("cr-extra-head", "cr-extra-head-end", doc);
  }

  function replaceAfterTheme(doc) {
    replaceHeadSlice("cr-after-theme", "cr-after-theme-end", doc);
  }

  function waitForNewStylesheets() {
    var links = Array.prototype.filter.call(
      document.querySelectorAll('link[rel="stylesheet"]'),
      function (link) {
        try {
          return !link.sheet;
        } catch (e) {
          return true;
        }
      }
    );
    if (!links.length) return Promise.resolve();
    return Promise.race([
      Promise.all(
        links.map(function (link) {
          return new Promise(function (resolve) {
            link.addEventListener("load", resolve, { once: true });
            link.addEventListener("error", resolve, { once: true });
          });
        })
      ),
      wait(250),
    ]);
  }

  function runScriptsBetween(start, end) {
    if (!start || !end) return;
    var node = start.nextSibling;
    var scripts = [];
    while (node && node !== end) {
      if (node.nodeType === 1 && node.tagName === "SCRIPT") scripts.push(node);
      else if (node.querySelectorAll) {
        Array.prototype.push.apply(scripts, node.querySelectorAll("script"));
      }
      node = node.nextSibling;
    }
    scripts.forEach(function (old) {
      var type = (old.getAttribute("type") || "").toLowerCase();
      if (type && type !== "text/javascript" && type !== "application/javascript" && type !== "module") {
        return;
      }
      var script = document.createElement("script");
      Array.prototype.slice.call(old.attributes).forEach(function (attr) {
        script.setAttribute(attr.name, attr.value);
      });
      if (old.src) script.async = false;
      else script.textContent = old.textContent;
      old.parentNode.replaceChild(script, old);
    });
  }

  function runScripts(root) {
    if (!root) return;
    Array.prototype.slice.call(root.querySelectorAll("script")).forEach(function (old) {
      var type = (old.getAttribute("type") || "").toLowerCase();
      if (type && type !== "text/javascript" && type !== "application/javascript" && type !== "module") {
        return;
      }
      var script = document.createElement("script");
      Array.prototype.slice.call(old.attributes).forEach(function (attr) {
        script.setAttribute(attr.name, attr.value);
      });
      if (old.src) {
        script.async = false;
      } else {
        script.textContent = old.textContent;
      }
      old.parentNode.replaceChild(script, old);
    });
  }

  function syncChrome(doc) {
    var pairs = [
      [".sidebar-nav", ".sidebar-nav"],
      [".sidebar-footer", ".sidebar-footer"],
    ];
    pairs.forEach(function (pair) {
      var current = document.querySelector(pair[0]);
      var next = doc.querySelector(pair[1]);
      if (current && next) current.innerHTML = next.innerHTML;
    });
  }

  function adoptToasts(doc) {
    var incoming = doc.getElementById("toast-stack");
    if (!incoming || !window.DaragaToast) return;
    incoming.querySelectorAll(".app-toast").forEach(function (toast) {
      var msg = toast.querySelector(".app-toast-msg");
      var tone = "info";
      ["success", "error", "warning", "info"].forEach(function (name) {
        if (toast.classList.contains("app-toast--" + name)) tone = name;
      });
      window.DaragaToast.show({
        tone: tone,
        message: msg ? msg.textContent : "",
      });
    });
  }

  function adminShellPath(url) {
    var path = String(url.pathname || "").replace(/\/+$/, "") || "/";
    return path === "/administration";
  }

  function reportsPath(url) {
    var path = String(url.pathname || "").replace(/\/+$/, "") || "/";
    return path === "/administration/reports";
  }

  function searchPath(url) {
    var path = String(url.pathname || "").replace(/\/+$/, "") || "/";
    return path === "/search";
  }

  function syncReportsChrome(doc) {
    var liveBar = document.querySelector(".reg-toolbar");
    var nextBar = doc.querySelector(".reg-toolbar");
    if (liveBar && nextBar) liveBar.innerHTML = nextBar.innerHTML;
  }

  function syncAdminChrome(doc) {
    var curTabs = document.querySelector(".admin-tabs");
    var nextTabs = doc.querySelector(".admin-tabs");
    if (curTabs && nextTabs) {
      var nextLinks = nextTabs.querySelectorAll("a.admin-tab");
      curTabs.querySelectorAll("a.admin-tab").forEach(function (link, index) {
        var next = nextLinks[index];
        if (!next) return;
        link.className = next.className;
        var href = next.getAttribute("href");
        if (href) link.setAttribute("href", href);
        var badge = link.querySelector(".admin-tab-badge");
        var nextBadge = next.querySelector(".admin-tab-badge");
        if (badge && nextBadge) {
          badge.className = nextBadge.className;
          badge.textContent = nextBadge.textContent;
          badge.hidden = nextBadge.hidden || nextBadge.hasAttribute("hidden");
        }
      });
    }
    var curHead = document.querySelector(".admin-page-header");
    var nextHead = doc.querySelector(".admin-page-header");
    if (curHead && nextHead) {
      curHead.className = nextHead.className;
      var state = curHead.querySelector(".page-state");
      var nextState = nextHead.querySelector(".page-state");
      if (state && nextState) state.textContent = nextState.textContent;
    }
  }

  function hardGo(url) {
    window.location.assign(url);
  }

  function wait(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  function visit(href, opts) {
    opts = opts || {};
    var url = new URL(href, location.href);
    if (!isSafeUrl(url)) {
      hardGo(url.href);
      return;
    }
    if (url.href === location.href && !opts.force) return;

    if (abortCtrl) {
      try {
        abortCtrl.abort();
      } catch (e) {}
    }
    abortCtrl = window.AbortController ? new AbortController() : null;
    var myGen = ++gen;
    busy = true;
    document.documentElement.classList.add("cr-navigating");
    page.classList.remove("is-entering");
    var panel = document.getElementById("admin-panel");
    var book = document.getElementById("reg-book");
    var results = document.getElementById("search-results");
    var panelNav = Boolean(panel && adminShellPath(url));
    var bookNav = Boolean(book && reportsPath(url) && reportsPath(location));
    var searchNav = Boolean(results && searchPath(url) && searchPath(location));
    if (panelNav) {
      panel.classList.remove("is-entering");
      panel.classList.add("is-leaving");
    } else if (bookNav) {
      book.classList.remove("is-entering");
      book.classList.add("is-leaving");
    } else if (searchNav) {
      results.classList.remove("is-entering");
      results.classList.add("is-leaving");
    } else {
      page.classList.add("is-leaving");
    }
    startProgress();

    var leaveMs = 0;
    var fetchOpts = {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "text/html", "X-CR-Nav": "1" },
      cache: "no-cache",
    };
    if (abortCtrl) fetchOpts.signal = abortCtrl.signal;

    Promise.all([
      fetch(url.href, fetchOpts),
      wait(leaveMs),
    ])
      .then(function (parts) {
        var response = parts[0];
        if (myGen !== gen) return null;
        if (!response.ok) throw new Error("nav-http");
        var finalUrl = new URL(response.url, location.href);
        if (!isSafeUrl(finalUrl)) {
          hardGo(finalUrl.href);
          return null;
        }
        var type = response.headers.get("content-type") || "";
        if (type && type.indexOf("text/html") === -1) {
          hardGo(finalUrl.href);
          return null;
        }
        return response.text().then(function (html) {
          return { html: html, finalUrl: finalUrl };
        });
      })
      .then(function (payload) {
        if (!payload || myGen !== gen) return;
        var parsed = new DOMParser().parseFromString(payload.html, "text/html");
        var nextPage = parsed.getElementById("cr-page") || parsed.querySelector("main.main");
        if (!nextPage) {
          hardGo(payload.finalUrl.href);
          return;
        }
        var nextPanel = parsed.getElementById("admin-panel");
        var nextBook = parsed.getElementById("reg-book");
        var stayInAdmin = Boolean(
          document.getElementById("admin-panel") &&
            nextPanel &&
            document.querySelector(".admin-tabs") &&
            parsed.querySelector(".admin-tabs") &&
            adminShellPath(payload.finalUrl)
        );
        var stayInReports = Boolean(
          document.getElementById("reg-book") &&
            nextBook &&
            reportsPath(payload.finalUrl) &&
            document.body.classList.contains("page-registry-reports")
        );
        var nextResults = parsed.getElementById("search-results");
        var stayInSearch = Boolean(
          document.getElementById("search-results") &&
            nextResults &&
            searchPath(payload.finalUrl) &&
            document.body.classList.contains("page-search")
        );
        if (stayInAdmin) {
          var livePanel = document.getElementById("admin-panel");
          livePanel.innerHTML = nextPanel.innerHTML;
          document.body.className = parsed.body ? parsed.body.className : document.body.className;
          if (parsed.title) document.title = parsed.title;
          syncAdminChrome(parsed);
          syncChrome(parsed);
          adoptToasts(parsed);
          runScripts(livePanel);
          page.scrollTop = 0;
          livePanel.scrollTop = 0;
          livePanel.classList.remove("is-leaving");
          livePanel.classList.add("is-entering");
        } else if (stayInReports) {
          var liveBook = document.getElementById("reg-book");
          syncReportsChrome(parsed);
          liveBook.innerHTML = nextBook.innerHTML;
          document.body.className = parsed.body ? parsed.body.className : document.body.className;
          if (parsed.title) document.title = parsed.title;
          syncChrome(parsed);
          adoptToasts(parsed);
          runScripts(liveBook);
          liveBook.scrollTop = 0;
          liveBook.classList.remove("is-leaving");
          liveBook.classList.add("is-entering");
        } else if (stayInSearch) {
          var liveResults = document.getElementById("search-results");
          liveResults.innerHTML = nextResults.innerHTML;
          document.body.className = parsed.body ? parsed.body.className : document.body.className;
          if (parsed.title) document.title = parsed.title;
          syncChrome(parsed);
          adoptToasts(parsed);
          runScripts(liveResults);
          liveResults.classList.remove("is-leaving");
          liveResults.classList.add("is-entering");
        } else {
          replaceExtraHead(parsed);
          replaceAfterTheme(parsed);
          runScriptsBetween(
            document.querySelector('meta[name="cr-extra-head"]'),
            document.querySelector('meta[name="cr-extra-head-end"]')
          );
          return waitForNewStylesheets().then(function () {
            if (myGen !== gen) return;
            if (parsed.body) document.body.className = parsed.body.className;
            if (parsed.title) document.title = parsed.title;
            page.innerHTML = nextPage.innerHTML;
            if (scriptsBox) {
              var nextScripts = parsed.getElementById("cr-page-scripts");
              scriptsBox.innerHTML = nextScripts ? nextScripts.innerHTML : "";
            }
            syncChrome(parsed);
            adoptToasts(parsed);
            runScripts(page);
            runScripts(scriptsBox);
            page.scrollTop = 0;
            page.classList.remove("is-leaving");
            page.classList.add("is-entering");
            if (opts.replace) {
              history.replaceState({ crNav: 1 }, "", payload.finalUrl.href);
            } else if (!opts.pop) {
              history.pushState({ crNav: 1 }, "", payload.finalUrl.href);
            }
            window.dispatchEvent(new CustomEvent("cr:page", { detail: { href: payload.finalUrl.href } }));
          });
        }
        if (opts.replace) {
          history.replaceState({ crNav: 1 }, "", payload.finalUrl.href);
        } else if (!opts.pop) {
          history.pushState({ crNav: 1 }, "", payload.finalUrl.href);
        }
        window.dispatchEvent(new CustomEvent("cr:page", { detail: { href: payload.finalUrl.href } }));
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        hardGo(url.href);
      })
      .then(function () {
        if (myGen !== gen) return;
        busy = false;
        document.documentElement.classList.remove("cr-navigating");
        finishProgress();
      });
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var anchor = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!anchor) return;
    markNav(anchor);
    if (!shouldInterceptAnchor(anchor)) return;
    event.preventDefault();
    visit(anchor.href);
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM") return;
    if (form.hasAttribute("data-no-page-transition")) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    if (form.target && form.target !== "_self" && form.target !== "") return;
    var action = new URL(form.getAttribute("action") || location.href, location.href);
    if (!isSafeUrl(action)) return;
    event.preventDefault();
    var next = new URL(action.href);
    next.search = "";
    var data = new FormData(form);
    data.forEach(function (value, key) {
      if (typeof File !== "undefined" && value instanceof File) return;
      next.searchParams.append(key, String(value));
    });
    visit(next.href);
  });

  window.addEventListener("popstate", function () {
    visit(location.href, { pop: true, force: true });
  });

  document.addEventListener(
    "pointerenter",
    function (event) {
      var anchor = event.target && event.target.closest ? event.target.closest("a[href]") : null;
      if (!shouldInterceptAnchor(anchor)) return;
      if (anchor.dataset.crPrefetch === "1") return;
      var next = new URL(anchor.href, location.href);
      if (next.pathname === location.pathname && next.search === location.search) return;
      anchor.dataset.crPrefetch = "1";
      var link = document.createElement("link");
      link.rel = "prefetch";
      link.href = next.href;
      document.head.appendChild(link);
    },
    true
  );
})();
