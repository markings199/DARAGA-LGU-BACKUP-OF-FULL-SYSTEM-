(function () {
  if (typeof window.__omCleanup === "function") {
    try {
      window.__omCleanup();
    } catch (err) {}
    window.__omCleanup = null;
  }

  var page = document.getElementById("om-page");
  if (!page) return;

  var EMOJI = [
    "😀","😁","😂","🤣","😊","😇","🙂","😉","😍","😘","😎","🤗","🤔","😅","😢","😭",
    "😮","😴","👍","👎","👏","🙏","💪","👌","✌️","🤝","❤️","💙","💚","💛","🧡","💜",
    "✨","⭐","🎉","✅","❌","⚠️","📌","📝","📅","⏰","☕","🍽️","📁","📎","💡","🙌"
  ];

  var find = document.getElementById("om-find");
  var people = document.getElementById("om-people");
  var emptyFind = document.getElementById("om-empty-find");
  if (find && people) {
    find.addEventListener("input", function () {
      var q = (find.value || "").trim().toLowerCase();
      var shown = 0;
      people.querySelectorAll(".om-person").forEach(function (link) {
        var name = (link.getAttribute("data-name") || "").toLowerCase();
        var match = !q || name.indexOf(q) !== -1;
        var item = link.closest("li");
        if (item) item.hidden = !match;
        if (match) shown += 1;
      });
      if (emptyFind) emptyFind.hidden = shown !== 0;
    });
  }

  var form = document.getElementById("om-compose");
  var box = document.getElementById("om-body");
  var scroll = document.getElementById("om-scroll");
  var sendUrl = page.getAttribute("data-send-url") || "";
  var pollUrl = page.getAttribute("data-poll-url") || "";
  var unsendUrl = page.getAttribute("data-unsend-url") || "";
  var pinUrl = page.getAttribute("data-pin-url") || "";
  var forwardUrl = page.getAttribute("data-forward-url") || "";
  var peerPhoto = page.getAttribute("data-peer-photo") || "";
  var peerName = page.getAttribute("data-peer-name") || "Office";
  var peerInitial = peerName.slice(0, 1).toUpperCase();
  var emojiBtn = document.getElementById("om-emoji-btn");
  var emojiPanel = document.getElementById("om-emoji-panel");
  var flyout = document.getElementById("om-flyout");
  var replyBar = document.getElementById("om-reply-bar");
  var replyName = document.getElementById("om-reply-name");
  var replyPreview = document.getElementById("om-reply-preview");
  var replyIdInput = document.getElementById("om-reply-id");
  var pinBar = document.getElementById("om-pin-bar");
  var pinJump = document.getElementById("om-pin-jump");
  var pinPreview = document.getElementById("om-pin-preview");
  var forwardBox = document.getElementById("om-forward");
  var forwardPeople = document.getElementById("om-forward-people");
  var store = {};
  var menuId = "";
  var forwardId = "";
  var replyTo = null;
  var pollTimer = 0;
  var onDocClick = null;
  var onDocDbl = null;
  var onDocKey = null;
  var onEmojiDocClick = null;

  function readJson(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent || "null") || fallback;
    } catch (err) {
      return fallback;
    }
  }

  readJson("om-thread-data", []).forEach(function (msg) {
    if (msg && msg.id) store[String(msg.id)] = msg;
  });
  var forwardContacts = readJson("om-forward-data", []);

  function lastId() {
    var nodes = scroll ? scroll.querySelectorAll("[data-msg-id]") : [];
    if (!nodes.length) return 0;
    return Number(nodes[nodes.length - 1].getAttribute("data-msg-id") || 0);
  }

  function escapeText(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatTime(iso, fallback) {
    if (!iso) return fallback || "";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return fallback || "";
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function formatDay(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  }

  function avatarHtml() {
    if (peerPhoto) {
      return '<span class="om-avatar om-avatar-sm has-photo"><img src="' + escapeText(peerPhoto) + '" alt="" width="28" height="28" decoding="async"></span>';
    }
    return '<span class="om-avatar om-avatar-sm">' + escapeText(peerInitial) + "</span>";
  }

  function quoteHtml(reply) {
    if (!reply) return "";
    return (
      '<button type="button" class="om-quote" data-jump="' +
      escapeText(reply.id) +
      '"><strong>' +
      escapeText(reply.name || "Message") +
      "</strong><span>" +
      escapeText(reply.unsent ? "Original message was unsent" : reply.body || "") +
      "</span></button>"
    );
  }

  function bubbleHtml(msg) {
    var unsent = !!msg.unsent;
    var text = String(msg.body || "").trim();
    var short = !unsent && text.length <= 8 && !/[A-Za-z0-9]/.test(text);
    var fwd = msg.forwarded && !unsent
      ? '<p class="om-fwd">Forwarded' + (msg.forwarded_from ? " · " + escapeText(msg.forwarded_from) : "") + "</p>"
      : "";
    var body = unsent
      ? '<p class="om-unsent-text">This message was unsent</p>'
      : "<p>" + escapeText(msg.body) + "</p>";
    return (
      '<article class="om-bubble' +
      (unsent ? " is-unsent" : short ? " is-emoji" : "") +
      '">' +
      fwd +
      body +
      '<time datetime="' +
      escapeText(msg.iso || "") +
      '">' +
      escapeText(formatTime(msg.iso, msg.at)) +
      "</time></article>"
    );
  }

  function rowHtml(msg) {
    var replyBtn = msg.unsent
      ? ""
      : '<button type="button" class="om-act" data-act="reply" title="Reply" aria-label="Reply">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 17l-5-5 5-5"/><path d="M20 18v-2a4 4 0 00-4-4H4"/></svg>' +
        "</button>";
    return (
      (msg.mine ? "" : avatarHtml()) +
      '<div class="om-stack">' +
      quoteHtml(msg.reply) +
      bubbleHtml(msg) +
      "</div>" +
      '<div class="om-actions">' +
      replyBtn +
      '<button type="button" class="om-act om-msg-more" data-act="more" aria-haspopup="true" aria-expanded="false" title="More" aria-label="More">⋯</button>' +
      "</div>"
    );
  }

  function ensureDay(iso) {
    var feed = document.getElementById("om-feed");
    if (!feed || !iso) return;
    var day = String(iso).slice(0, 10);
    if (!day || feed.querySelector('[data-om-day="' + day + '"]')) return;
    var mark = document.createElement("p");
    mark.className = "om-day";
    mark.innerHTML = "<span data-om-day=\"" + escapeText(day) + "\">" + escapeText(formatDay(iso)) + "</span>";
    feed.appendChild(mark);
  }

  function appendMessage(msg) {
    if (!msg || !msg.id || !scroll) return;
    store[String(msg.id)] = msg;
    if (scroll.querySelector('[data-msg-id="' + msg.id + '"]')) return;
    var empty = document.getElementById("om-thread-empty");
    if (empty) empty.remove();
    var feed = document.getElementById("om-feed") || scroll;
    ensureDay(msg.iso);
    var row = document.createElement("div");
    row.className = "om-row" + (msg.mine ? " is-mine" : "") + (msg.pinned ? " is-pinned" : "");
    row.setAttribute("data-msg-id", String(msg.id));
    row.innerHTML = rowHtml(msg);
    feed.appendChild(row);
    scroll.scrollTop = scroll.scrollHeight;
  }

  function localizeTimes() {
    page.querySelectorAll(".om-bubble time[datetime]").forEach(function (el) {
      el.textContent = formatTime(el.getAttribute("datetime"), el.textContent);
    });
    page.querySelectorAll("[data-om-day]").forEach(function (el) {
      el.textContent = formatDay(el.getAttribute("data-om-day") + "T00:00:00");
    });
  }

  function closeFlyout() {
    if (!flyout) return;
    flyout.hidden = true;
    menuId = "";
    page.querySelectorAll(".om-row.is-open").forEach(function (row) {
      row.classList.remove("is-open");
    });
    page.querySelectorAll(".om-msg-more").forEach(function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function openFlyout(btn, id) {
    if (!flyout) return;
    var msg = store[String(id)] || {};
    var pinBtn = document.getElementById("om-flyout-pin");
    var everyone = document.getElementById("om-flyout-everyone");
    if (pinBtn) pinBtn.textContent = msg.pinned ? "Unpin" : "Pin";
    if (everyone) everyone.hidden = !msg.can_unsend_everyone;
    flyout.querySelector('[data-act="reply"]').hidden = !!msg.unsent;
    flyout.querySelector('[data-act="forward"]').hidden = !!msg.unsent;
    if (pinBtn) pinBtn.hidden = !!msg.unsent;
    menuId = String(id);
    flyout.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    page.querySelectorAll(".om-row.is-open").forEach(function (row) {
      row.classList.remove("is-open");
    });
    var openRow = btn.closest("[data-msg-id]");
    if (openRow) openRow.classList.add("is-open");
    var box = btn.getBoundingClientRect();
    var menu = flyout.getBoundingClientRect();
    var left = msg.mine ? box.right - menu.width : box.left;
    var top = box.bottom + 6;
    if (left < 8) left = 8;
    if (left + menu.width > window.innerWidth - 8) left = window.innerWidth - menu.width - 8;
    if (top + menu.height > window.innerHeight - 8) top = Math.max(8, box.top - menu.height - 6);
    flyout.style.left = left + "px";
    flyout.style.top = top + "px";
  }

  function jumpTo(id) {
    var row = scroll ? scroll.querySelector('[data-msg-id="' + id + '"]') : null;
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-flash");
    window.setTimeout(function () {
      row.classList.remove("is-flash");
    }, 1400);
  }

  function setReply(msg) {
    if (!msg || msg.unsent) return;
    replyTo = msg;
    if (replyIdInput) replyIdInput.value = String(msg.id);
    if (replyName) replyName.textContent = msg.mine ? "yourself" : peerName;
    if (replyPreview) replyPreview.textContent = msg.body || "";
    if (replyBar) replyBar.hidden = false;
    if (box) box.focus();
  }

  function clearReply() {
    replyTo = null;
    if (replyIdInput) replyIdInput.value = "";
    if (replyBar) replyBar.hidden = true;
  }

  function setPinned(msg) {
    page.querySelectorAll(".om-row.is-pinned").forEach(function (row) {
      row.classList.remove("is-pinned");
    });
    if (!pinBar) return;
    if (!msg) {
      pinBar.hidden = true;
      pinBar.classList.add("is-empty");
      if (pinJump) pinJump.setAttribute("data-jump", "");
      if (pinPreview) pinPreview.textContent = "";
      return;
    }
    store[String(msg.id)] = Object.assign({}, store[String(msg.id)] || {}, msg, { pinned: true });
    var row = scroll ? scroll.querySelector('[data-msg-id="' + msg.id + '"]') : null;
    if (row) row.classList.add("is-pinned");
    pinBar.hidden = false;
    pinBar.classList.remove("is-empty");
    if (pinJump) pinJump.setAttribute("data-jump", String(msg.id));
    if (pinPreview) pinPreview.textContent = msg.unsent ? "This message was unsent" : (msg.body || "");
  }

  function confirmAction(opts) {
    if (window.DaragaConfirm && typeof window.DaragaConfirm.ask === "function") {
      return window.DaragaConfirm.ask(opts);
    }
    return Promise.resolve(window.confirm(opts.message || ""));
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify(payload || {}),
    }).then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, data: data };
      });
    });
  }

  function markUnsent(id) {
    var key = String(id);
    var current = store[key] || {};
    store[key] = Object.assign({}, current, {
      unsent: true,
      body: "",
      can_unsend_everyone: false,
      forwarded: false,
      pinned: false,
    });
    var row = scroll ? scroll.querySelector('[data-msg-id="' + id + '"]') : null;
    if (!row) return;
    row.classList.remove("is-pinned");
    var stack = row.querySelector(".om-stack");
    if (stack) {
      var quote = stack.querySelector(".om-quote");
      stack.innerHTML = (quote ? quote.outerHTML : "") + bubbleHtml(store[key]);
    }
    var replyBtn = row.querySelector('[data-act="reply"]');
    if (replyBtn) replyBtn.remove();
    if (pinJump && pinJump.getAttribute("data-jump") === key) setPinned(null);
  }

  function removeMessage(id) {
    var row = scroll ? scroll.querySelector('[data-msg-id="' + id + '"]') : null;
    if (row) row.remove();
    delete store[String(id)];
    var feed = document.getElementById("om-feed");
    if (feed && !feed.querySelector("[data-msg-id]") && !document.getElementById("om-thread-empty")) {
      var empty = document.createElement("p");
      empty.className = "om-thread-empty";
      empty.id = "om-thread-empty";
      empty.textContent = "No messages yet. Write below to start.";
      feed.appendChild(empty);
    }
    if (pinJump && pinJump.getAttribute("data-jump") === String(id)) setPinned(null);
  }

  function unsendMessage(id, scope) {
    if (!unsendUrl || !id) return;
    var everyone = scope === "everyone";
    confirmAction({
      title: everyone ? "Unsend for everyone" : "Unsend for me",
      message: everyone
        ? "Remove this message for both of you? The other person will see that it was unsent."
        : "Remove this message from your conversation only? The other person can still see it.",
      okLabel: everyone ? "Unsend for everyone" : "Unsend for me",
      cancelLabel: "Keep message",
      danger: true,
    }).then(function (ok) {
      if (!ok) return;
      postJson(unsendUrl, { message_id: id, scope: scope })
        .then(function (result) {
          if (!result.ok || !result.data || !result.data.ok) return;
          if (result.data.hidden) removeMessage(id);
          else markUnsent(id);
        })
        .catch(function () {});
    });
  }

  function pinMessage(id, pinned) {
    if (!pinUrl || !id) return;
    postJson(pinUrl, { message_id: id, pinned: pinned })
      .then(function (result) {
        if (!result.ok || !result.data || !result.data.ok) return;
        Object.keys(store).forEach(function (key) {
          if (store[key]) store[key].pinned = false;
        });
        setPinned(result.data.pinned || null);
      })
      .catch(function () {});
  }

  function openForward(id) {
    var msg = store[String(id)];
    if (!msg || msg.unsent || !forwardBox || !forwardPeople) return;
    forwardId = String(id);
    forwardPeople.innerHTML = "";
    if (!forwardContacts.length) {
      forwardPeople.innerHTML = "<li class=\"om-overlay-empty\">No other office account is available.</li>";
    } else {
      forwardContacts.forEach(function (person) {
        var item = document.createElement("li");
        item.innerHTML =
          '<button type="button" data-forward-to="' +
          escapeText(person.id) +
          '"><span class="om-avatar om-avatar-sm' +
          (person.photo_url ? " has-photo" : "") +
          '">' +
          (person.photo_url
            ? '<img src="' + escapeText(person.photo_url) + '" alt="" width="28" height="28">'
            : escapeText((person.username || "?").slice(0, 1).toUpperCase())) +
          "</span><strong>" +
          escapeText(person.username) +
          "</strong></button>";
        forwardPeople.appendChild(item);
      });
    }
    forwardBox.hidden = false;
  }

  function closeForward() {
    if (forwardBox) forwardBox.hidden = true;
    forwardId = "";
  }

  function forwardMessage(toUserId) {
    if (!forwardUrl || !forwardId || !toUserId) return;
    postJson(forwardUrl, { message_id: forwardId, to_user_id: toUserId })
      .then(function (result) {
        if (!result.ok || !result.data || !result.data.ok) return;
        closeForward();
        if (result.data.message && String(page.getAttribute("data-peer-id") || "") === String(toUserId)) {
          appendMessage(result.data.message);
        }
      })
      .catch(function () {});
  }

  function handleAction(act, id) {
    var msg = store[String(id)];
    if (!msg) return;
    if (act === "reply") setReply(msg);
    if (act === "pin") pinMessage(id, !msg.pinned);
    if (act === "forward") openForward(id);
    if (act === "unsend-everyone") unsendMessage(id, "everyone");
    if (act === "unsend-me") unsendMessage(id, "me");
  }

  onDocClick = function (event) {
    var jump = event.target.closest ? event.target.closest("[data-jump]") : null;
    if (jump && page.contains(jump)) {
      event.preventDefault();
      jumpTo(jump.getAttribute("data-jump"));
      return;
    }
    var more = event.target.closest ? event.target.closest('[data-act="more"]') : null;
    if (more && page.contains(more)) {
      event.preventDefault();
      event.stopPropagation();
      var row = more.closest("[data-msg-id]");
      var id = row ? row.getAttribute("data-msg-id") : "";
      if (menuId === id && flyout && !flyout.hidden) closeFlyout();
      else openFlyout(more, id);
      return;
    }
    var actBtn = event.target.closest ? event.target.closest("[data-act]") : null;
    if (actBtn && (page.contains(actBtn) || (flyout && flyout.contains(actBtn)))) {
      event.preventDefault();
      var act = actBtn.getAttribute("data-act");
      var fromRow = actBtn.closest("[data-msg-id]");
      var targetId = (fromRow && fromRow.getAttribute("data-msg-id")) || menuId;
      closeFlyout();
      handleAction(act, targetId);
      return;
    }
    var to = event.target.closest ? event.target.closest("[data-forward-to]") : null;
    if (to) {
      event.preventDefault();
      forwardMessage(to.getAttribute("data-forward-to"));
      return;
    }
    if (forwardBox && !forwardBox.hidden && event.target === forwardBox) closeForward();
    closeFlyout();
  };
  document.addEventListener("click", onDocClick);

  onDocDbl = function (event) {
    var bubble = event.target.closest ? event.target.closest(".om-bubble") : null;
    if (!bubble || !page.contains(bubble) || bubble.classList.contains("is-unsent")) return;
    var row = bubble.closest("[data-msg-id]");
    var msg = row ? store[row.getAttribute("data-msg-id")] : null;
    if (msg) setReply(msg);
  };
  document.addEventListener("dblclick", onDocDbl);

  onDocKey = function (event) {
    if (event.key === "Escape") {
      closeFlyout();
      closeForward();
      closeEmoji();
    }
  };
  document.addEventListener("keydown", onDocKey);

  if (document.getElementById("om-reply-cancel")) {
    document.getElementById("om-reply-cancel").addEventListener("click", clearReply);
  }
  if (document.getElementById("om-pin-clear")) {
    document.getElementById("om-pin-clear").addEventListener("click", function () {
      var id = pinJump ? pinJump.getAttribute("data-jump") : "";
      if (id) pinMessage(id, false);
    });
  }
  if (document.getElementById("om-forward-cancel")) {
    document.getElementById("om-forward-cancel").addEventListener("click", closeForward);
  }

  function insertEmoji(symbol) {
    if (!box) return;
    var start = box.selectionStart || 0;
    var end = box.selectionEnd || 0;
    var value = box.value || "";
    box.value = value.slice(0, start) + symbol + value.slice(end);
    var pos = start + symbol.length;
    box.selectionStart = box.selectionEnd = pos;
    box.focus();
  }

  function emojiIsOpen() {
    return !!(emojiPanel && emojiPanel.classList.contains("is-open"));
  }

  function closeEmoji() {
    if (!emojiPanel || !emojiBtn) return;
    emojiPanel.hidden = true;
    emojiPanel.classList.remove("is-open");
    emojiBtn.setAttribute("aria-expanded", "false");
  }

  function openEmoji() {
    if (!emojiPanel || !emojiBtn) return;
    emojiPanel.hidden = false;
    emojiPanel.classList.add("is-open");
    emojiBtn.setAttribute("aria-expanded", "true");
  }

  if (emojiBtn && emojiPanel) {
    closeEmoji();
    EMOJI.forEach(function (symbol) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = symbol;
      btn.setAttribute("role", "option");
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        insertEmoji(symbol);
      });
      emojiPanel.appendChild(btn);
    });
    emojiBtn.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (emojiIsOpen()) closeEmoji();
      else openEmoji();
    });
    onEmojiDocClick = function (event) {
      if (!emojiIsOpen()) return;
      if (emojiBtn.contains(event.target) || emojiPanel.contains(event.target)) return;
      closeEmoji();
    };
    document.addEventListener("click", onEmojiDocClick);
  }

  localizeTimes();
  var existingPin = pinJump && pinJump.getAttribute("data-jump");
  if (existingPin && store[existingPin]) setPinned(store[existingPin]);

  window.__omCleanup = function () {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = 0;
    if (onDocClick) document.removeEventListener("click", onDocClick);
    if (onDocDbl) document.removeEventListener("dblclick", onDocDbl);
    if (onDocKey) document.removeEventListener("keydown", onDocKey);
    if (onEmojiDocClick) document.removeEventListener("click", onEmojiDocClick);
    closeFlyout();
    closeForward();
  };

  if (!form || !box || !scroll || !sendUrl) {
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
    return;
  }

  function sendBody(text) {
    var body = String(text || "").replace(/\u0000/g, "").trim();
    if (!body) return;
    var replyId = replyTo ? replyTo.id : "";
    box.value = "";
    closeEmoji();
    clearReply();
    postJson(sendUrl, { body: body, reply_to_id: replyId || undefined })
      .then(function (result) {
        if (!result.ok || !result.data || !result.data.ok) {
          box.value = body;
          if (replyId && store[String(replyId)]) setReply(store[String(replyId)]);
          return;
        }
        appendMessage(result.data.message);
      })
      .catch(function () {
        box.value = body;
        if (replyId && store[String(replyId)]) setReply(store[String(replyId)]);
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    sendBody(box.value);
  });

  box.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendBody(box.value);
    }
  });

  scroll.scrollTop = scroll.scrollHeight;

  if (pollUrl) {
    pollTimer = window.setInterval(function () {
      fetch(pollUrl + (pollUrl.indexOf("?") >= 0 ? "&" : "?") + "after=" + lastId(), {
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(function (res) {
          return res.ok ? res.json() : null;
        })
        .then(function (data) {
          if (!data || !data.ok) return;
          (data.messages || []).forEach(appendMessage);
          (data.unsent_ids || []).forEach(markUnsent);
          setPinned(data.pinned || null);
        })
        .catch(function () {});
    }, 6000);
  }
})();
