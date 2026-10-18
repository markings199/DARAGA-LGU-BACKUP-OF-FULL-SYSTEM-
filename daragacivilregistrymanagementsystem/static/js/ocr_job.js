(function () {
  var bar = document.getElementById("ocr-job-bar");
  if (!bar) return;

  var activeUrl = bar.getAttribute("data-active-url");
  var statusTpl = bar.getAttribute("data-status-url") || "";
  var cancelUrl = bar.getAttribute("data-cancel-url") || "";
  var pctEl = document.getElementById("ocr-job-bar-pct");
  var fillEl = document.getElementById("ocr-job-bar-fill");
  var labelEl = document.getElementById("ocr-job-bar-label");
  var openEl = document.getElementById("ocr-job-bar-open");
  var cancelEl = document.getElementById("ocr-job-bar-cancel");
  var pollTimer = null;
  var currentJobId = null;
  var shownPct = 1;
  var openFormOnDone = false;
  var pollInFlight = false;
  var pollAbort = null;

  function statusUrl(jobId) {
    return statusTpl.replace("JOB_ID", encodeURIComponent(jobId));
  }

  function onScanPage() {
    return !!document.getElementById("scan-form");
  }

  function onWaitPage() {
    return document.body.classList.contains("page-ocr-wait");
  }

  function onResultForm() {
    return /\/ocr\/result\//.test(window.location.pathname);
  }

  var smoothTimer = null;

  function stopSmoothProgress() {
    if (smoothTimer) {
      clearInterval(smoothTimer);
      smoothTimer = null;
    }
  }

  function startSmoothProgress() {
    if (smoothTimer) return;
    smoothTimer = setInterval(function () {
      if (shownPct >= 90) return;
      var step = shownPct < 35 ? 2 : 1;
      setPct(shownPct + step);
    }, 450);
  }

  function setPct(value) {
    var n = Math.max(1, Math.min(100, Number(value) || 1));
    shownPct = n;
    if (pctEl) pctEl.textContent = n + "%";
    if (fillEl) fillEl.style.width = n + "%";
    var pageFill = document.getElementById("ocr-progress-fill");
    var pageText = document.getElementById("ocr-progress-text");
    if (pageFill) pageFill.style.width = n + "%";
    if (pageText) pageText.textContent = n + "%";
    if (window.DaragaOcrTargets && typeof window.DaragaOcrTargets.setProgress === "function") {
      window.DaragaOcrTargets.setProgress(n);
    }
  }

  function fileLabel(path) {
    if (!path) return "";
    var parts = String(path).split("/");
    return parts[parts.length - 1] || path;
  }

  function lockScanInputs(locked) {
    ["document_type", "image", "detect-scanner", "num_scans", "document_type_override", "doc-type-change-btn", "scanner-device-select", "scan-source-upload", "scan-source-scanner"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.disabled = !!locked;
    });
    var scanBtn = document.getElementById("scan-from-device");
    if (scanBtn) {
      if (locked) {
        scanBtn.disabled = true;
      } else if (window.DaragaScanner && typeof window.DaragaScanner.sync === "function") {
        window.DaragaScanner.sync();
      } else {
        var sel = document.getElementById("scanner-device-select");
        scanBtn.disabled = !(sel && sel.value);
      }
    }
  }

  function scanDocumentPresent() {
    if (!onScanPage()) return false;
    var wrap = document.getElementById("scan-preview-viewer-wrap");
    var fileInput = document.getElementById("image");
    var hid = document.getElementById("image_filename");
    if (wrap && !wrap.hidden) return true;
    if (fileInput && fileInput.files && fileInput.files.length) return true;
    if (hid && hid.value) return true;
    return false;
  }

  function setCancelVisible(show) {
    if (window.DaragaScanPreview && typeof window.DaragaScanPreview.setCancelVisible === "function") {
      window.DaragaScanPreview.setCancelVisible(show);
      return;
    }
    var btnCancel = document.getElementById("btn-ocr-cancel");
    if (!btnCancel) return;
    if (show) {
      btnCancel.removeAttribute("hidden");
      btnCancel.hidden = false;
    } else {
      btnCancel.hidden = true;
      btnCancel.setAttribute("hidden", "");
    }
  }

  function restoreScanDocument(data) {
    if (!onScanPage() || !data) return;
    var dt = document.getElementById("document_type");
    if (dt && data.doc_type) {
      dt.value = data.doc_type;
      if (window.DaragaDocType && typeof window.DaragaDocType.apply === "function") {
        window.DaragaDocType.apply(data.doc_type);
      }
      if (window.DaragaOcrTargets && typeof window.DaragaOcrTargets.setType === "function") {
        window.DaragaOcrTargets.setType();
      }
    }
    var hid = document.getElementById("image_filename");
    if (hid && data.image_filename) hid.value = data.image_filename;
    var status = document.getElementById("ocr-image-in-use");
    var name = fileLabel(data.image_filename);
    if (status) {
      if (name) {
        status.hidden = false;
        status.textContent = "Selected: " + name;
      } else {
        status.hidden = true;
        status.textContent = "";
      }
    }
    var fnEl = document.getElementById("scan-preview-filename");
    if (fnEl && name) {
      fnEl.textContent = name;
      fnEl.hidden = false;
    }
    if (data.image_url) {
      var wrap = document.getElementById("scan-preview-viewer-wrap");
      var empty = document.getElementById("scan-preview-empty");
      var img = document.getElementById("scan-wf-viewer-image");
      if (wrap && img && empty) {
        if (img.getAttribute("src") !== data.image_url) {
          img.src = data.image_url;
        }
        empty.hidden = true;
        wrap.hidden = false;
        setCancelVisible(true);
        var sec = document.getElementById("scan-wf-preview-viewer");
        if (sec && sec.__dvReapplyZoom) {
          requestAnimationFrame(function () {
            sec.__dvReapplyZoom();
          });
        }
      }
    }
  }

  function showScanLoading() {
    var ocrLoading = document.getElementById("ocr-loading");
    var btnOcr = document.getElementById("btn-ocr");
    if (ocrLoading) {
      ocrLoading.classList.add("is-on");
      ocrLoading.setAttribute("aria-busy", "true");
      ocrLoading.style.setProperty("display", "block", "important");
    }
    document.body.classList.add("ocr-busy");
    if (btnOcr) {
      btnOcr.disabled = true;
      btnOcr.classList.add("is-running");
      var ocrLbl = btnOcr.querySelector(".btn-ocr-label");
      if (ocrLbl) ocrLbl.textContent = "Running OCR…";
    }
    setCancelVisible(true);
    var state = document.getElementById("workflow-state");
    var next = document.getElementById("workflow-next");
    if (state) state.textContent = "OCR PROCESSING";
    if (next) next.textContent = "Please wait";
    lockScanInputs(true);
    if (window.DaragaOcrTargets && typeof window.DaragaOcrTargets.start === "function") {
      window.DaragaOcrTargets.start();
    }
  }

  function clearScanDocumentPreview() {
    if (!onScanPage()) return;
    if (window.DaragaScanPreview && typeof window.DaragaScanPreview.clear === "function") {
      try {
        window.DaragaScanPreview.clear();
        return;
      } catch (e) {}
    }
    var wrap = document.getElementById("scan-preview-viewer-wrap");
    var empty = document.getElementById("scan-preview-empty");
    var img = document.getElementById("scan-wf-viewer-image");
    var fileInput = document.getElementById("image");
    var hid = document.getElementById("image_filename");
    var fnEl = document.getElementById("scan-preview-filename");
    var status = document.getElementById("ocr-image-in-use");
    if (img) {
      img.onload = null;
      img.removeAttribute("src");
      img.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
    }
    if (wrap) wrap.hidden = true;
    if (empty) empty.hidden = false;
    if (fileInput) {
      try {
        fileInput.value = "";
      } catch (e) {}
    }
    if (hid) hid.value = "";
    if (fnEl) {
      fnEl.textContent = "";
      fnEl.hidden = true;
    }
    if (status) {
      status.hidden = true;
      status.textContent = "";
    }
    if (window.DaragaDocType && typeof window.DaragaDocType.reset === "function") {
      window.DaragaDocType.reset();
    }
    setCancelVisible(false);
  }

  function hideScanLoading() {
    var ocrLoading = document.getElementById("ocr-loading");
    var btnOcr = document.getElementById("btn-ocr");
    document.body.classList.remove("ocr-busy");
    if (btnOcr) {
      btnOcr.classList.remove("is-running");
      var ocrLbl = btnOcr.querySelector(".btn-ocr-label");
      if (ocrLbl) ocrLbl.textContent = "Run OCR";
      if (window.DaragaOcrButton && typeof window.DaragaOcrButton.sync === "function") {
        window.DaragaOcrButton.sync();
      } else {
        var dt = document.getElementById("document_type");
        var typed = dt && /^(birth|marriage|death)$/.test(String(dt.value || "").trim().toLowerCase());
        btnOcr.disabled = !typed;
      }
    }
    setCancelVisible(scanDocumentPresent());
    if (ocrLoading) {
      ocrLoading.classList.remove("is-on");
      ocrLoading.style.setProperty("display", "none", "important");
      ocrLoading.setAttribute("aria-busy", "false");
    }
    lockScanInputs(false);
    var state = document.getElementById("workflow-state");
    var next = document.getElementById("workflow-next");
    if (state) state.textContent = "—";
    if (next) next.textContent = "Scan";
    if (window.DaragaOcrTargets && typeof window.DaragaOcrTargets.stop === "function") {
      window.DaragaOcrTargets.stop();
    }
  }

  function showBar(running) {
    bar.hidden = false;
    bar.classList.add("is-on");
    bar.setAttribute("aria-busy", running ? "true" : "false");
    if (openEl) openEl.hidden = running;
    if (cancelEl) cancelEl.hidden = !running;
    if (labelEl) labelEl.textContent = running ? "OCR running" : "OCR ready";
  }

  function hideBar() {
    bar.hidden = true;
    bar.classList.remove("is-on");
    bar.setAttribute("aria-busy", "false");
    if (openEl) openEl.hidden = true;
    if (cancelEl) cancelEl.hidden = true;
  }

  var pollGen = 0;

  function stopPoll() {
    pollGen += 1;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    pollInFlight = false;
    if (pollAbort) {
      try {
        pollAbort.abort();
      } catch (e) {}
      pollAbort = null;
    }
  }

  function pollDelay() {
    if (document.hidden) return 4000;
    if (onWaitPage() || onScanPage()) return 700;
    return 1500;
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, pollDelay());
  }

  function clearLocalOcrUi() {
    stopPoll();
    currentJobId = null;
    openFormOnDone = false;
    stopSmoothProgress();
    hideBar();
    if (onScanPage()) hideScanLoading();
  }

  function cancelOcr() {
    var jobId = currentJobId;
    currentJobId = null;
    openFormOnDone = false;
    stopPoll();
    stopSmoothProgress();
    clearScanDocumentPreview();
    hideBar();
    if (onScanPage()) hideScanLoading();
    if (!cancelUrl) return;
    var body = jobId ? JSON.stringify({ job_id: jobId }) : "{}";
    fetch(cancelUrl, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      credentials: "same-origin",
      body: body,
    }).catch(function () {});
  }

  function handlePayload(data) {
    if (!data || !data.ok) return;
    if (!currentJobId) return;
    if (data.job_id && String(data.job_id) !== String(currentJobId)) return;
    var status = data.status || "";
    if (data.progress) {
      var incoming = Number(data.progress) || 0;
      if (incoming > shownPct) setPct(incoming);
    }
    restoreScanDocument(data);
    if (status === "queued" || status === "running") {
      showBar(true);
      startSmoothProgress();
      if (onScanPage()) showScanLoading();
      if (openEl) {
        openEl.hidden = true;
        openEl.removeAttribute("href");
      }
      return;
    }
    if (status === "error" || status === "cancelled") {
      currentJobId = null;
      stopSmoothProgress();
      stopPoll();
      hideBar();
      if (onScanPage()) hideScanLoading();
      if (status === "cancelled") clearScanDocumentPreview();
      if (status === "error") {
        if (window.DaragaToast && window.DaragaToast.error) {
          window.DaragaToast.error(data.error, "OCR could not read this document. Try a clearer scan.");
        }
      }
      return;
    }
    if (status === "done" && data.redirect_url) {
      currentJobId = null;
      stopSmoothProgress();
      stopPoll();
      hideBar();
      if (onScanPage()) hideScanLoading();
      if (onWaitPage() || openFormOnDone) {
        window.location.href = data.redirect_url;
      }
      return;
    }
  }

  function poll() {
    var gen = pollGen;
    if (!currentJobId) return;
    if (document.hidden || pollInFlight) {
      schedulePoll();
      return;
    }
    pollInFlight = true;
    pollAbort = typeof AbortController !== "undefined" ? new AbortController() : null;
    fetch(statusUrl(currentJobId), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      signal: pollAbort ? pollAbort.signal : undefined,
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return r.ok ? j : Promise.reject(j);
        });
      })
      .then(function (data) {
        if (gen !== pollGen) return;
        handlePayload(data);
      })
      .catch(function () {})
      .then(function () {
        if (gen !== pollGen) return;
        pollInFlight = false;
        pollAbort = null;
        if (currentJobId) schedulePoll();
      });
  }

  function follow(jobId, seed) {
    if (!jobId) return;
    currentJobId = jobId;
    openFormOnDone = onWaitPage() || onScanPage();
    stopPoll();
    if (seed) restoreScanDocument(seed);
    if (seed && seed.progress) setPct(seed.progress);
    else setPct(shownPct > 1 ? shownPct : 8);
    showBar(true);
    startSmoothProgress();
    if (onScanPage()) showScanLoading();
    poll();
  }

  function resume() {
    if (!activeUrl) return;
    fetch(activeUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.completed && data.redirect_url && onWaitPage()) {
          window.location.href = data.redirect_url;
          return;
        }
        if (!data || !data.active || !data.job_id) {
          clearLocalOcrUi();
          return;
        }
        if (data.status === "queued" || data.status === "running") {
          follow(data.job_id, data);
          return;
        }
        clearLocalOcrUi();
      })
      .catch(function () {
        clearLocalOcrUi();
      });
  }

  if (cancelEl) {
    cancelEl.addEventListener("click", function () {
      cancelOcr();
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!currentJobId || document.hidden) return;
    stopPoll();
    poll();
  });

  window.addEventListener("pagehide", function () {
    stopPoll();
  });

  window.DaragaOcr = {
    follow: follow,
    resume: resume,
    cancel: cancelOcr,
  };

  resume();
})();
