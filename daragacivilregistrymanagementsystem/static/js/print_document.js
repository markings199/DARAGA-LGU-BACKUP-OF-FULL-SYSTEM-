(function () {
  "use strict";

  var PRINT_MODES = { original: true, certification: true, both: true };

  function lockedPrintFormat() {
    var btn = document.getElementById("btn-print-document");
    if (!btn) return "";
    var locked = (btn.getAttribute("data-locked-print-format") || "").trim();
    return PRINT_MODES[locked] ? locked : "";
  }

  function selectedPrintMode() {
    var locked = lockedPrintFormat();
    if (locked) return locked;
    var checked = document.querySelector('input[name="print_mode"]:checked');
    if (checked && PRINT_MODES[checked.value]) return checked.value;
    var cert = document.getElementById("print-area-certification");
    var orig = document.getElementById("print-area-original");
    if (orig && cert) return "both";
    if (cert && !orig) return "certification";
    return "original";
  }

  function setActivePrintSurface(mode) {
    var orig = document.getElementById("print-area-original");
    var cert = document.getElementById("print-area-certification");
    var showOrig = mode === "original" || mode === "both";
    var showCert = mode === "certification" || mode === "both";
    if (orig) orig.classList.toggle("print-surface-active", showOrig);
    if (cert) cert.classList.toggle("print-surface-active", showCert);
  }

  var CERT_BLANK_KEYS = {
    "Page Number": true,
    "Book Number": true,
    "Amount Paid": true,
    "OR Number": true,
    "Date Paid": true,
    "Issued To": true,
  };
  var CERT_BLANK_LABELS = {
    "Page Number": "Register page",
    "Book Number": "Book number",
    "Amount Paid": "Amount paid",
    "OR Number": "OR number",
    "Date Paid": "Date paid",
    "Issued To": "Issued to",
  };

  function certPrintNeedsBlanks() {
    var mode = selectedPrintMode();
    return mode === "certification" || mode === "both";
  }

  function certBlankValue(input) {
    var key = input.getAttribute("data-cert-blank-input");
    var value = String(input.value || "").trim();
    if (key === "Amount Paid") {
      value = value.replace(/^[₱P]\s*/i, "").trim();
    }
    return value;
  }

  function markCertBlankState(input, incomplete) {
    input.classList.toggle("is-incomplete", incomplete);
    input.setAttribute("aria-invalid", incomplete ? "true" : "false");
    var field = input.closest(".print-setup-field");
    if (field) field.classList.toggle("is-incomplete", incomplete);
  }

  function missingCertBlanks() {
    var missing = [];
    document.querySelectorAll("[data-cert-blank-input]").forEach(function (input) {
      var key = input.getAttribute("data-cert-blank-input");
      var empty = !certBlankValue(input);
      markCertBlankState(input, empty);
      if (empty) {
        missing.push({
          input: input,
          label: CERT_BLANK_LABELS[key] || key || "Required field",
        });
      }
    });
    return missing;
  }

  function metaTextFilled(text) {
    var t = String(text || "")
      .replace(/\u00a0/g, " ")
      .replace(/[—–]/g, "-")
      .trim();
    return t.length > 0 && t !== "-";
  }

  function reviewWrapFilled(wrap) {
    var radios = wrap.querySelectorAll("input[type='radio']");
    if (radios.length) {
      return Array.prototype.some.call(radios, function (radio) {
        return radio.checked && String(radio.value || "").trim();
      });
    }
    var input = wrap.querySelector("input:not([type='hidden']), textarea, select");
    if (input) return String(input.value || "").trim().length > 0;
    var valueEl = wrap.querySelector(".rd-meta-value");
    if (valueEl) return metaTextFilled(valueEl.textContent);
    return true;
  }

  function wrapLabel(wrap) {
    var label = wrap.querySelector(".confirm-item-label, .rd-meta-label, label");
    var text = label ? String(label.textContent || "").replace(/\*/g, "").trim() : "";
    return text || wrap.getAttribute("data-required-meta-field") || "Required field";
  }

  function missingRequiredMeta(btn) {
    var missing = [];
    var seen = {};
    document.querySelectorAll("[data-review-required='1'], [data-required-meta-field]").forEach(function (wrap) {
      var key = wrap.getAttribute("data-required-meta-field") || wrapLabel(wrap);
      var empty = !reviewWrapFilled(wrap);
      wrap.classList.toggle("is-missing", empty);
      if (!empty || seen[key]) return;
      seen[key] = true;
      missing.push({
        wrap: wrap,
        input: wrap.querySelector("input:not([type='hidden']), textarea, select"),
        label: wrapLabel(wrap),
        key: key,
      });
    });
    if (!missing.length && btn) {
      try {
        var stored = JSON.parse(btn.getAttribute("data-missing-meta") || "[]") || [];
        stored.forEach(function (key) {
          if (!key || seen[key]) return;
          seen[key] = true;
          missing.push({
            wrap: document.getElementById("extracted-metadata"),
            input: null,
            label: key,
            key: key,
          });
        });
      } catch (e) {}
    }
    return missing;
  }

  function updatePrintMetaBanner(missing) {
    var box = document.getElementById("print-missing-meta");
    var list = document.getElementById("print-missing-meta-list");
    var btn = document.getElementById("btn-print-document");
    if (list) {
      list.innerHTML = "";
      missing.forEach(function (row) {
        var item = document.createElement("li");
        item.textContent = row.label;
        list.appendChild(item);
      });
    }
    if (box) box.classList.toggle("is-hidden", !missing.length);
    if (btn) {
      btn.setAttribute("aria-disabled", missing.length ? "true" : "false");
      btn.setAttribute("data-missing-meta", JSON.stringify(missing.map(function (row) {
        return row.key || row.label;
      })));
    }
  }

  function notifyRequiredMeta(missing) {
    updatePrintMetaBanner(missing);
    var names = missing
      .map(function (row) {
        return row.label;
      })
      .join(", ");
    var editable = missing.some(function (row) {
      return row.input;
    });
    var message = editable
      ? "Fill these Extracted metadata fields before printing: " + names + "."
      : "These Extracted metadata fields are empty: " +
        names +
        ". Open Extracted metadata, then use Edit record (admin) or Request edit (staff).";
    if (window.DaragaToast && window.DaragaToast.show) {
      window.DaragaToast.show({
        tone: "error",
        title: "Missing extracted metadata",
        message: message,
      });
    } else {
      window.alert(message);
    }
    var first = missing[0] && (missing[0].input || missing[0].wrap);
    var heading = document.getElementById("extracted-metadata");
    var target = first || heading;
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (missing[0] && missing[0].input) {
      try {
        missing[0].input.focus();
      } catch (e) {}
    }
  }

  function notifyCertBlanksRequired(missing) {
    var names = missing
      .map(function (row) {
        return row.label;
      })
      .join(", ");
    var message =
      "Fill in " +
      names +
      " before printing. Admin already approved this request — complete the register and payment details first.";
    if (window.DaragaToast && window.DaragaToast.show) {
      window.DaragaToast.show({
        tone: "warning",
        title: "Complete these fields first",
        message: message,
      });
    } else {
      window.alert(message);
    }
    var first = missing[0] && missing[0].input;
    if (first) {
      if (typeof first.scrollIntoView === "function") {
        first.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      try {
        first.focus();
      } catch (e) {}
    }
  }

  function certBlankStorageKey() {
    var panel = document.querySelector(".print-panel");
    var rid = panel && panel.getAttribute("data-record-id");
    return "dcr-cert-blanks:" + (rid || "draft");
  }

  function readStoredCertBlanks() {
    try {
      var raw = sessionStorage.getItem(certBlankStorageKey());
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function storeCertBlanks() {
    var out = {};
    document.querySelectorAll("[data-cert-blank-input]").forEach(function (input) {
      var key = input.getAttribute("data-cert-blank-input");
      if (key) out[key] = String(input.value || "").trim();
    });
    try {
      sessionStorage.setItem(certBlankStorageKey(), JSON.stringify(out));
    } catch (e) {}
  }

  function setCertBlankText(key, value) {
    var text = String(value || "").trim() || "\u00a0";
    document.querySelectorAll('[data-cert-blank="' + key + '"]').forEach(function (el) {
      el.textContent = text;
    });
  }

  function syncCertBlanksFromPanel() {
    document.querySelectorAll("[data-cert-blank-input]").forEach(function (input) {
      var key = input.getAttribute("data-cert-blank-input");
      if (!key) return;
      var value = String(input.value || "").trim();
      if (key === "Amount Paid") {
        value = value.replace(/^[₱P]\s*/i, "");
      }
      setCertBlankText(key, value);
    });
  }

  function restoreCertBlanks() {
    var stored = readStoredCertBlanks();
    document.querySelectorAll("[data-cert-blank-input]").forEach(function (input) {
      var key = input.getAttribute("data-cert-blank-input");
      if (!key) return;
      if (Object.prototype.hasOwnProperty.call(stored, key) && stored[key]) {
        input.value = stored[key];
      }
    });
    syncCertBlanksFromPanel();
  }

  function syncCertificationFromForm() {
    var form = document.querySelector("form[method='post']");
    if (!form) return;
    document.querySelectorAll("[data-cert-field]").forEach(function (el) {
      var key = el.getAttribute("data-cert-field");
      if (!key || key === "Issued To" || CERT_BLANK_KEYS[key]) return;
      var input = form.elements[key];
      if (!input) return;
      var v = String(input.value || "").trim();
      el.textContent = v || " ";
    });
  }

  window.syncCertificationFromForm = syncCertificationFromForm;

  function applyPrintLayout() {
    var paper = document.getElementById("print-paper-size");
    var font = document.getElementById("print-font-family");
    var paperVal = paper && paper.value ? paper.value : "legal";
    var fontVal = font && font.value ? font.value : "arial";
    var papers = {
      legal: { page: "8.5in 13in", w: "8.5in", h: "13in" },
      "us-legal": { page: "8.5in 14in", w: "8.5in", h: "14in" },
      letter: { page: "letter", w: "8.5in", h: "11in" },
      a4: { page: "A4", w: "210mm", h: "297mm" },
    };
    var p = papers[paperVal] || papers.legal;
    var mode = selectedPrintMode();
    var defaultMargin = mode === "certification" ? "0.5in 0.75in 0.5in 1in" : "0";
    document.body.classList.toggle("print-original-only", mode === "original");
    var pageStyle = document.getElementById("print-page-style");
    if (!pageStyle) {
      pageStyle = document.createElement("style");
      pageStyle.id = "print-page-style";
      document.head.appendChild(pageStyle);
    }
    pageStyle.textContent =
      "@media print { @page { size: " +
      p.page +
      " portrait; margin: " +
      defaultMargin +
      "; } " +
      "@page original-scan { size: " +
      p.page +
      " portrait; margin: 0; } " +
      "@page cert-form { size: " +
      p.page +
      " portrait; margin: 0.5in 0.75in 0.5in 1in; } " +
      "#print-area-original.print-surface-active { page: original-scan; width: " +
      p.w +
      "; height: 100vh; max-width: " +
      p.w +
      "; max-height: none; min-height: " +
      p.h +
      "; } " +
      "#print-area-original.print-surface-active .print-cert-image { width: " +
      p.w +
      " !important; height: 100vh !important; min-height: " +
      p.h +
      " !important; max-width: " +
      p.w +
      " !important; max-height: none !important; object-fit: fill; object-position: top center; margin: 0; } }";
    document.querySelectorAll(".cert-print-surface").forEach(function (el) {
      el.setAttribute("data-print-font", fontVal);
    });
    [
      ["print-signer-verifier", "verifier"],
      ["print-signer-registrar", "registrar"],
      ["print-signer-officer", "officer"],
    ].forEach(function (pair) {
      var input = document.getElementById(pair[0]);
      if (!input) return;
      var name = String(input.value || "").trim();
      document.querySelectorAll('[data-signer-slot="' + pair[1] + '"]').forEach(function (el) {
        el.textContent = name || " ";
      });
    });
    [
      ["print-signer-verifier-title", "verifier"],
      ["print-signer-registrar-title", "registrar"],
    ].forEach(function (pair) {
      var input = document.getElementById(pair[0]);
      if (!input) return;
      var title = String(input.value || "").trim();
      document.querySelectorAll('[data-signer-title="' + pair[1] + '"]').forEach(function (el) {
        el.textContent = title || " ";
      });
    });
  }

  function runPrint(btn) {
    var mode = selectedPrintMode();
    if (btn.getAttribute("data-sync-form") === "1") {
      syncCertificationFromForm();
    }
    syncCertBlanksFromPanel();
    applyPrintLayout();
    setActivePrintSurface(mode);

    var printLogUrl = btn.getAttribute("data-print-log-url");
    var payload = { print_type: mode };

    function triggerPrint() {
      window.print();
    }

    if (!printLogUrl) {
      triggerPrint();
      return;
    }

    fetch(printLogUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        var ctype = String(r.headers.get("content-type") || "").toLowerCase();
        if (ctype.indexOf("json") === -1) {
          return {
            httpOk: false,
            data: {
              ok: false,
              error:
                r.status === 401
                  ? "Please log in again to print."
                  : "Print could not start. Refresh the page and try again.",
            },
          };
        }
        return r.json().then(
          function (data) {
            return { httpOk: r.ok, data: data };
          },
          function () {
            return {
              httpOk: false,
              data: { ok: false, error: "Print could not start. Refresh the page and try again." },
            };
          }
        );
      })
      .then(function (result) {
        var data = result.data || {};
        if (!result.httpOk || !data.ok) {
          var serverMissing = data.missing || [];
          if (serverMissing.length) {
            notifyRequiredMeta(
              serverMissing.map(function (label) {
                return { label: label, wrap: document.getElementById("extracted-metadata"), input: null };
              })
            );
            return;
          }
          if (window.DaragaToast && window.DaragaToast.show) {
            window.DaragaToast.show({
              tone: "error",
              title: "Print was not allowed",
              message: data.error || "Print could not start. Refresh the page and try again.",
            });
          }
          return;
        }
        triggerPrint();
      })
      .catch(function () {
        if (window.DaragaToast && window.DaragaToast.show) {
          window.DaragaToast.show({
            tone: "error",
            title: "Print was not allowed",
            message: "Print could not start. Refresh the page and try again.",
          });
        }
      });
  }

  var printUiBound = false;

  function bindPrintUi() {
    var btn = document.getElementById("btn-print-document");
    if (!btn || printUiBound || window.__dcrPrintUiBound) return;
    printUiBound = true;
    window.__dcrPrintUiBound = true;

    var mode = selectedPrintMode();
    setActivePrintSurface(mode);
    applyPrintLayout();
    var fontSel = document.getElementById("print-font-family");
    if (fontSel) fontSel.addEventListener("change", applyPrintLayout);
    var paperSel = document.getElementById("print-paper-size");
    if (paperSel) paperSel.addEventListener("change", applyPrintLayout);
    restoreCertBlanks();
    updatePrintMetaBanner(missingRequiredMeta(btn));
    document.addEventListener("input", function (event) {
      if (!event.target || !event.target.closest("[data-review-required='1']")) return;
      updatePrintMetaBanner(missingRequiredMeta(btn));
    });
    document.addEventListener("change", function (event) {
      if (!event.target || !event.target.closest("[data-review-required='1']")) return;
      updatePrintMetaBanner(missingRequiredMeta(btn));
    });
    document.querySelectorAll("[data-cert-blank-input]").forEach(function (input) {
      input.addEventListener("input", function () {
        storeCertBlanks();
        syncCertBlanksFromPanel();
        if (certBlankValue(input)) markCertBlankState(input, false);
      });
    });

    var locked = lockedPrintFormat();
    document.querySelectorAll('input[name="print_mode"]').forEach(function (radio) {
      var card = radio.closest(".print-option-card");
      if (locked) {
        radio.disabled = true;
        if (card) card.classList.add("is-disabled");
        return;
      }
      radio.disabled = false;
      if (card) card.classList.remove("is-disabled");
      radio.addEventListener("change", function () {
        setActivePrintSurface(selectedPrintMode());
      });
    });

    btn.addEventListener("click", function (event) {
      event.preventDefault();
      if (btn.getAttribute("data-can-print") !== "1") {
        if (window.DaragaToast && window.DaragaToast.show) {
          window.DaragaToast.show({
            tone: "warning",
            title: "Please check",
            message: "Admin approval is required before you can print this document.",
          });
        }
        return;
      }
      var missingMeta = missingRequiredMeta(btn);
      if (missingMeta.length) {
        notifyRequiredMeta(missingMeta);
        return;
      }
      if (certPrintNeedsBlanks()) {
        var missing = missingCertBlanks();
        if (missing.length) {
          notifyCertBlanksRequired(missing);
          return;
        }
      }
      runPrint(btn);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindPrintUi);
  } else {
    bindPrintUi();
  }
})();
