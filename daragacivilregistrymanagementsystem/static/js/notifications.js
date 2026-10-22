(function () {
  var wrap = document.getElementById("notif-wrap");
  if (!wrap) return;

  var btn = document.getElementById("notif-bell");
  var panel = document.getElementById("notif-panel");
  var list = document.getElementById("notif-list");
  var empty = document.getElementById("notif-empty");
  var countEl = document.getElementById("notif-count");
  var markAll = document.getElementById("notif-mark-all");
  var url = wrap.getAttribute("data-url");
  var readUrl = wrap.getAttribute("data-read-url");
  var readAllUrl = wrap.getAttribute("data-read-all-url");

  function setCount(n) {
    var total = Number(n) || 0;
    wrap.setAttribute("data-count", String(total));
    try {
      sessionStorage.setItem("cr-notif-count", String(total));
    } catch (e) {}
    if (countEl) {
      countEl.textContent = total > 99 ? "99+" : String(total);
      countEl.hidden = total < 1;
    }
    btn.setAttribute("aria-label", total ? "Notifications, " + total + " unread" : "Notifications");
    if (markAll) markAll.hidden = total < 1;
    var adminNav = document.querySelector('.sidebar-nav a[data-nav="admin"] .nav-badge');
    var editNav = document.querySelector('.sidebar-nav a[data-nav="my_edit_requests"] .nav-badge');
    var printNav = document.querySelector('.sidebar-nav a[data-nav="my_print_requests"] .nav-badge');
    return { adminNav: adminNav, editNav: editNav, printNav: printNav };
  }

  function setNavBadge(el, n) {
    if (!el) return;
    var total = Number(n) || 0;
    el.textContent = total > 99 ? "99+" : String(total);
    el.hidden = total < 1;
  }

  function timeAgo(iso) {
    if (!iso) return "";
    var then = Date.parse(iso);
    if (!then) return "";
    var sec = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (sec < 60) return "Just now";
    var min = Math.round(sec / 60);
    if (min < 60) return min + "m ago";
    var hr = Math.round(min / 60);
    if (hr < 24) return hr + "h ago";
    var day = Math.round(hr / 24);
    return day + "d ago";
  }

  function postJson(endpoint, body) {
    if (!endpoint) return Promise.reject();
    return fetch(endpoint, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      if (!res.ok) throw new Error("notif");
      return res.json();
    });
  }

  function render(data) {
    var badges = (data && data.badges) || {};
    var items = (data && data.items) || [];
    var nav = setCount(data && data.count);
    setNavBadge(nav.adminNav, (badges.admin_pending_edit_requests || 0) + (badges.admin_pending_print_requests || 0));
    setNavBadge(nav.editNav, (badges.staff_my_requests_pending || 0) + (badges.staff_my_requests_approved_ready || 0));
    setNavBadge(nav.printNav, (badges.staff_print_pending || 0) + (badges.staff_print_requests_approved_ready || 0));
    document.querySelectorAll("[data-admin-badge]").forEach(function (el) {
      var key = el.getAttribute("data-admin-badge");
      var total = Number(badges[key] || 0);
      if (key === "backup") {
        el.textContent = total > 0 ? "!" : "0";
      } else {
        el.textContent = total > 99 ? "99+" : String(total);
      }
      el.hidden = total < 1;
    });

    if (!list) return;
    list.innerHTML = "";
    if (!items.length) {
      if (empty) empty.hidden = false;
      if (markAll) markAll.hidden = true;
      return;
    }
    if (empty) empty.hidden = true;
    items.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "notif-item" + (item.read ? " is-read" : "");
      var a = document.createElement("a");
      a.className = "notif-item-main";
      a.href = item.url || "#";
      a.innerHTML =
        '<span class="notif-item-dot" aria-hidden="true"></span>' +
        '<span class="notif-item-copy">' +
        '<span class="notif-item-title"></span>' +
        '<span class="notif-item-body"></span>' +
        '<span class="notif-item-time"></span>' +
        "</span>";
      a.querySelector(".notif-item-title").textContent = item.title || "Update";
      a.querySelector(".notif-item-body").textContent = item.body || "";
      a.querySelector(".notif-item-time").textContent = timeAgo(item.at);
      a.addEventListener("click", function () {
        if (!item.read && item.id) {
          postJson(readUrl, { id: item.id, read: true }).then(render).catch(function () {});
        }
      });
      row.appendChild(a);
      if (item.detail) {
        var det = document.createElement("details");
        det.className = "notif-item-more";
        var sum = document.createElement("summary");
        sum.textContent = "Show details";
        var p = document.createElement("p");
        p.textContent = item.detail;
        det.appendChild(sum);
        det.appendChild(p);
        det.addEventListener("click", function (ev) {
          ev.stopPropagation();
        });
        row.appendChild(det);
      }
      if (item.id) {
        var actions = document.createElement("div");
        actions.className = "notif-item-actions";
        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "notif-read-btn";
        toggle.textContent = item.read ? "Mark as unread" : "Mark as read";
        toggle.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          postJson(readUrl, { id: item.id, read: !item.read }).then(render).catch(function () {});
        });
        actions.appendChild(toggle);
        row.appendChild(actions);
      }
      list.appendChild(row);
    });
  }

  var loadTimer = null;
  var loadInFlight = false;

  function load() {
    if (!url) return;
    if (document.hidden) return;
    if (loadInFlight) return;
    loadInFlight = true;
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("notif");
        return res.json();
      })
      .then(render)
      .catch(function () {})
      .then(function () {
        loadInFlight = false;
      });
  }

  btn.addEventListener("click", function (ev) {
    ev.stopPropagation();
    var open = wrap.classList.toggle("is-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) load();
  });

  if (markAll) {
    markAll.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      postJson(readAllUrl, {}).then(render).catch(function () {});
    });
  }

  document.addEventListener("click", function (ev) {
    if (!wrap.contains(ev.target)) {
      wrap.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      wrap.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    }
  });

  load();
  loadTimer = setInterval(load, 15000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) load();
  });
})();

(function () {
  function placeNotePanel(fold) {
    var panel = fold.querySelector(".note-fold-panel");
    var summary = fold.querySelector("summary");
    if (!panel || !summary) return;
    var r = summary.getBoundingClientRect();
    var width = Math.min(360, Math.max(240, Math.min(r.width + 120, window.innerWidth - 24)));
    var left = r.left;
    if (left + width > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - width - 12);
    }
    var top = r.bottom + 6;
    var maxH = Math.min(260, window.innerHeight - top - 16);
    panel.style.position = "fixed";
    panel.style.left = left + "px";
    panel.style.width = width + "px";
    panel.style.zIndex = "240";
    if (maxH < 110 && r.top > 140) {
      panel.style.maxHeight = Math.min(260, r.top - 18) + "px";
      panel.style.top = "0px";
      requestAnimationFrame(function () {
        var h = panel.getBoundingClientRect().height;
        panel.style.top = Math.max(12, r.top - 6 - h) + "px";
      });
      return;
    }
    panel.style.top = top + "px";
    panel.style.maxHeight = Math.max(110, maxH) + "px";
  }

  function clearNotePanel(fold) {
    var panel = fold.querySelector(".note-fold-panel");
    if (!panel) return;
    panel.style.position = "";
    panel.style.left = "";
    panel.style.top = "";
    panel.style.width = "";
    panel.style.maxHeight = "";
    panel.style.zIndex = "";
  }

  document.addEventListener(
    "toggle",
    function (ev) {
      var el = ev.target;
      if (!el || !el.classList || !el.classList.contains("note-fold")) return;
      if (el.open) {
        document.querySelectorAll("details.note-fold[open]").forEach(function (other) {
          if (other !== el) other.open = false;
        });
        placeNotePanel(el);
      } else {
        clearNotePanel(el);
      }
    },
    true
  );

  function repositionOpenNotes() {
    document.querySelectorAll("details.note-fold[open]").forEach(placeNotePanel);
  }
  window.addEventListener("resize", repositionOpenNotes);
  document.addEventListener("scroll", repositionOpenNotes, true);

  document.addEventListener("pointerdown", function (ev) {
    if (ev.target.closest && ev.target.closest("details.note-fold")) return;
    document.querySelectorAll("details.note-fold[open]").forEach(function (fold) {
      fold.open = false;
    });
  });
})();
