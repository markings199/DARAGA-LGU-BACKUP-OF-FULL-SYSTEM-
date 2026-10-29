(function () {
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
  var peerPhoto = page.getAttribute("data-peer-photo") || "";
  var peerName = (page.getAttribute("data-peer-name") || "?").slice(0, 1).toUpperCase();
  var emojiBtn = document.getElementById("om-emoji-btn");
  var emojiPanel = document.getElementById("om-emoji-panel");

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

  function avatarHtml() {
    if (peerPhoto) {
      return '<span class="om-avatar om-avatar-sm has-photo"><img src="' + escapeText(peerPhoto) + '" alt="" width="28" height="28" decoding="async"></span>';
    }
    return '<span class="om-avatar om-avatar-sm">' + escapeText(peerName) + "</span>";
  }

  function appendMessage(msg) {
    if (!msg || !msg.id || !scroll) return;
    if (scroll.querySelector('[data-msg-id="' + msg.id + '"]')) return;
    var empty = document.getElementById("om-thread-empty");
    if (empty) empty.remove();
    var feed = document.getElementById("om-feed") || scroll;
    var row = document.createElement("div");
    row.className = "om-row" + (msg.mine ? " is-mine" : "");
    row.setAttribute("data-msg-id", String(msg.id));
    var text = String(msg.body || "").trim();
    var short = text.length <= 8 && !/[A-Za-z0-9]/.test(text);
    row.innerHTML =
      (msg.mine ? "" : avatarHtml()) +
      '<article class="om-bubble' + (short ? " is-emoji" : "") + '"><p>' +
      escapeText(msg.body) +
      "</p><time>" +
      escapeText(msg.at || "") +
      "</time></article>";
    feed.appendChild(row);
    scroll.scrollTop = scroll.scrollHeight;
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
    document.addEventListener("click", function (event) {
      if (!emojiIsOpen()) return;
      if (emojiBtn.contains(event.target) || emojiPanel.contains(event.target)) return;
      closeEmoji();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeEmoji();
    });
  }

  if (!form || !box || !scroll || !sendUrl) {
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
    return;
  }

  function sendBody(text) {
    var body = String(text || "").replace(/\u0000/g, "").trim();
    if (!body) return;
    box.value = "";
    closeEmoji();
    fetch(sendUrl, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify({ body: body }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data || !result.data.ok) {
          box.value = body;
          return;
        }
        appendMessage(result.data.message);
      })
      .catch(function () {
        box.value = body;
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
    window.setInterval(function () {
      fetch(pollUrl + (pollUrl.indexOf("?") >= 0 ? "&" : "?") + "after=" + lastId(), {
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(function (res) {
          return res.ok ? res.json() : null;
        })
        .then(function (data) {
          if (!data || !data.ok || !data.messages) return;
          data.messages.forEach(appendMessage);
        })
        .catch(function () {});
    }, 6000);
  }
})();
