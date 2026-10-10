(function () {
  var form = document.querySelector("[data-restore-ajax]");
  var overlay = document.getElementById("restore-progress");
  if (!form || !overlay) return;

  var stepEl = overlay.querySelector("#restore-progress-step");
  var timeEl = overlay.querySelector("#restore-progress-time");
  var bar = overlay.querySelector(".restore-progress-bar");
  var fill = overlay.querySelector(".restore-progress-fill");
  var errEl = overlay.querySelector(".restore-progress-error");
  var startedAt = 0;
  var tickId = 0;
  var cycleId = 0;
  var xhr = null;
  var waitStarted = false;
  var waitSteps = [
    "Reading documents from the backup zip…",
    "Saving certificate scans…",
    "Adding restored records…",
  ];

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
    if (fill) fill.style.width = (indeterminate ? 32 : value) + "%";
    if (bar) bar.setAttribute("aria-valuenow", indeterminate ? 0 : String(value));
  }

  function stopTimers() {
    if (tickId) window.clearInterval(tickId);
    if (cycleId) window.clearInterval(cycleId);
    tickId = 0;
    cycleId = 0;
  }

  function openOverlay() {
    startedAt = Date.now();
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = "";
    }
    waitStarted = false;
    overlay.hidden = false;
    overlay.classList.remove("is-error");
    document.body.classList.add("restore-progress-open");
    setStep("Uploading backup file…");
    setBar(2, false);
    if (timeEl) timeEl.textContent = elapsedText();
    tickId = window.setInterval(function () {
      if (timeEl) timeEl.textContent = elapsedText();
    }, 250);
  }

  function startWaitCycle() {
    if (waitStarted) return;
    waitStarted = true;
    var i = 0;
    setStep(waitSteps[0]);
    setBar(100, true);
    cycleId = window.setInterval(function () {
      i = (i + 1) % waitSteps.length;
      setStep(waitSteps[i]);
    }, 2500);
  }

  function fail(message) {
    stopTimers();
    overlay.hidden = false;
    overlay.classList.add("is-error");
    overlay.classList.remove("is-indeterminate");
    setStep("Restore did not finish");
    if (fill) fill.style.width = "100%";
    if (errEl) {
      errEl.hidden = false;
      errEl.textContent = message || "Restore failed. Try the same Full backup again.";
    }
    document.body.classList.remove("restore-progress-open");
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn-secondary restore-progress-close";
    close.textContent = "Close";
    close.addEventListener("click", function () {
      overlay.hidden = true;
      overlay.classList.remove("is-error");
      if (close.parentNode) close.parentNode.removeChild(close);
    });
    if (!overlay.querySelector(".restore-progress-close")) {
      overlay.querySelector(".restore-progress-panel").appendChild(close);
    }
  }

  function readBody(request) {
    var data = request.response;
    if (data && typeof data === "object") return data;
    try {
      return JSON.parse(request.responseText || "{}");
    } catch (e) {
      return {};
    }
  }

  function submitRestore() {
    var fileInput = form.querySelector('input[type="file"][name="backup_file"]');
    if (!fileInput || !fileInput.files || !fileInput.files.length) {
      fail("Choose a backup zip first.");
      return;
    }
    openOverlay();
    xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.setRequestHeader("X-Daraga-Restore", "1");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.responseType = "text";
    xhr.timeout = 8 * 60 * 1000;
    xhr.ontimeout = function () {
      fail("Full rebuild took too long. Close this, restart Start.bat, then try the same zip again.");
    };
    xhr.upload.onprogress = function (event) {
      if (!event.lengthComputable) return;
      var pct = (event.loaded / event.total) * 88;
      setBar(Math.max(4, pct), false);
      setStep("Uploading backup file… " + Math.round((event.loaded / event.total) * 100) + "%");
      if (event.loaded >= event.total) startWaitCycle();
    };
    xhr.upload.onload = function () {
      startWaitCycle();
    };
    xhr.onerror = function () {
      fail("The connection dropped while restoring. Check the server and try again.");
    };
    xhr.onload = function () {
      var payload = readBody(xhr);
      if (xhr.status >= 200 && xhr.status < 300 && payload.ok) {
        stopTimers();
        setBar(100, false);
        setStep("Restore complete. Reloading…");
        window.location.href = payload.redirect || form.getAttribute("action") || window.location.href;
        return;
      }
      fail(payload.message || "Restore failed. Previous records were kept if possible.");
    };
    xhr.send(new FormData(form));
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var mode = form.querySelector('input[name="restore_mode"]:checked');
    var titleEl = overlay.querySelector("#restore-progress-title");
    var isFull = mode && mode.value === "full";
    waitSteps = isFull
      ? ["Rebuilding records…", "Copying scans…", "Finishing…"]
      : ["Reading documents…", "Saving scans…", "Adding records…"];
    if (titleEl) titleEl.textContent = isFull ? "Full rebuild" : "Restoring documents";
    var ask = window.DaragaConfirm && window.DaragaConfirm.ask;
    var opts = {
      title: (mode && mode.getAttribute("data-confirm-title")) || "Restore",
      message: (mode && mode.getAttribute("data-confirm-message")) || "",
      okLabel: (mode && mode.getAttribute("data-confirm-ok")) || "Restore",
      cancelLabel: form.getAttribute("data-confirm-cancel") || "Cancel",
      danger: !mode || mode.getAttribute("data-confirm-danger") === "1",
    };
    if (!ask) {
      submitRestore();
      return;
    }
    ask(opts).then(function (ok) {
      if (ok) submitRestore();
    });
  });
})();
