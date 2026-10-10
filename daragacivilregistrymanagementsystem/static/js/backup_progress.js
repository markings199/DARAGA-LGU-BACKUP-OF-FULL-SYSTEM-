(function () {
  var form = document.getElementById("auto-backup-form");
  var overlay = document.getElementById("backup-progress");
  if (!form || !overlay) return;

  var titleEl = overlay.querySelector("#backup-progress-title");
  var stepEl = overlay.querySelector("#backup-progress-step");
  var timeEl = overlay.querySelector("#backup-progress-time");
  var bar = overlay.querySelector(".restore-progress-bar");
  var fill = overlay.querySelector(".restore-progress-fill");
  var startedAt = 0;
  var tickId = 0;
  var cycleId = 0;
  var open = false;
  var starting = false;

  var STEPS = {
    full: [
      "Gathering records, users, and scans…",
      "Writing the full system zip…",
      "Copying to the Daraga LGU server…",
      "Verifying the backup file…",
    ],
    period: [
      "Collecting certificates for this period…",
      "Writing the backup zip…",
      "Copying to the Daraga LGU server…",
      "Finishing…",
    ],
    copy: [
      "Connecting to the Daraga LGU server…",
      "Sending waiting backup files…",
      "Verifying the office copy…",
      "Finishing…",
    ],
  };

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function elapsedText() {
    var sec = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    return "Elapsed " + pad(Math.floor(sec / 60)) + ":" + pad(sec % 60);
  }

  function setStep(text) {
    if (stepEl) stepEl.textContent = text;
  }

  function setBar(pct, indeterminate) {
    overlay.classList.toggle("is-indeterminate", !!indeterminate);
    var value = Math.max(0, Math.min(100, Math.round(pct)));
    if (fill) fill.style.width = (indeterminate ? 36 : value) + "%";
    if (bar) bar.setAttribute("aria-valuenow", indeterminate ? 0 : String(value));
  }

  function runKind(btn) {
    if (!btn || !btn.getAttribute) return "";
    var marked = (btn.getAttribute("data-backup-run") || "").toLowerCase();
    if (marked === "full" || marked === "period" || marked === "copy") return marked;
    return "";
  }

  function openOverlay(mode) {
    if (open) return;
    open = true;
    startedAt = Date.now();
    if (overlay.parentNode !== document.body) {
      document.body.appendChild(overlay);
    }
    overlay.hidden = false;
    overlay.removeAttribute("hidden");
    overlay.classList.remove("is-error");
    overlay.style.display = "flex";
    document.body.classList.add("restore-progress-open");
    var steps = STEPS[mode] || STEPS.period;
    if (titleEl) {
      titleEl.textContent =
        mode === "full" ? "Creating full system backup" : mode === "copy" ? "Saving backup copy" : "Creating backup";
    }
    setStep(steps[0]);
    setBar(12, true);
    if (timeEl) timeEl.textContent = elapsedText();
    var i = 0;
    tickId = window.setInterval(function () {
      if (timeEl) timeEl.textContent = elapsedText();
      var elapsed = (Date.now() - startedAt) / 1000;
      setBar(Math.min(88, 12 + elapsed * 3), true);
    }, 250);
    cycleId = window.setInterval(function () {
      i = (i + 1) % steps.length;
      setStep(steps[i]);
    }, 2800);
  }

  function injectedKind() {
    var input = form.querySelector("input[name='backup_kind'][data-backup-kind-inject='1']");
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "backup_kind";
      input.setAttribute("data-backup-kind-inject", "1");
      form.appendChild(input);
    }
    return input;
  }

  function startBackup(btn) {
    if (starting) return;
    starting = true;
    var mode = runKind(btn) || "period";
    openOverlay(mode);
    injectedKind().value = btn.value || (mode === "full" ? "full" : "daily");
    var action = btn.getAttribute("formaction");
    if (action) form.setAttribute("action", action);
    window.setTimeout(function () {
      HTMLFormElement.prototype.submit.call(form);
    }, 40);
  }

  form.addEventListener(
    "click",
    function (event) {
      var btn = event.target && event.target.closest ? event.target.closest("[data-backup-run]") : null;
      if (!btn || btn.disabled) return;
      event.preventDefault();
      event.stopPropagation();
      var mode = runKind(btn);
      if (!mode) return;
      if (btn.hasAttribute("data-app-confirm")) {
        var ask = window.DaragaConfirm && window.DaragaConfirm.ask;
        var opts = {
          title: btn.getAttribute("data-confirm-title") || "Full system backup",
          message: btn.getAttribute("data-confirm-message") || "",
          okLabel: btn.getAttribute("data-confirm-ok") || "Create full backup",
          cancelLabel: btn.getAttribute("data-confirm-cancel") || "Go back",
          danger: false,
        };
        if (!ask) {
          startBackup(btn);
          return;
        }
        ask(opts).then(function (ok) {
          if (ok) startBackup(btn);
        });
        return;
      }
      startBackup(btn);
    },
    true
  );
})();
