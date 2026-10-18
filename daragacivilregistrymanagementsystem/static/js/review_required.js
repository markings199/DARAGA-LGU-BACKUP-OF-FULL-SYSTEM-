(function () {
  function skippedSubmitter(submitter) {
    if (!submitter) return false;
    if (submitter.hasAttribute("formnovalidate")) return true;
    var action = (submitter.getAttribute("formaction") || "").toLowerCase();
    return action.indexOf("cancel") !== -1;
  }

  function fieldFilled(wrap) {
    var radios = wrap.querySelectorAll("input[type='radio']");
    if (radios.length) {
      return Array.prototype.some.call(radios, function (radio) {
        return radio.checked && String(radio.value || "").trim();
      });
    }
    var input = wrap.querySelector("input:not([type='hidden']), textarea, select");
    if (!input) return true;
    return String(input.value || "").trim().length > 0;
  }

  function requiredWraps(form) {
    return Array.prototype.slice.call(form.querySelectorAll("[data-review-required='1']"));
  }

  function clearMissing(wrap) {
    wrap.classList.remove("is-missing");
  }

  function showToast(count) {
    var message =
      count === 1
        ? "Fill the highlighted required field before saving."
        : "Fill the " + count + " highlighted required fields before saving.";
    if (window.DaragaToast && typeof window.DaragaToast.show === "function") {
      window.DaragaToast.show({
        tone: "error",
        title: "Required fields",
        message: message,
      });
      return;
    }
    window.alert(message);
  }

  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      if (!form || form.getAttribute("data-required-review") !== "1") return;
      if (skippedSubmitter(event.submitter)) return;
      var wraps = requiredWraps(form);
      var missing = wraps.filter(function (wrap) {
        return !fieldFilled(wrap);
      });
      wraps.forEach(function (wrap) {
        wrap.classList.toggle("is-missing", missing.indexOf(wrap) !== -1);
      });
      if (!missing.length) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      var first = missing[0];
      if (first && typeof first.scrollIntoView === "function") {
        first.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      var focusEl = first
        ? first.querySelector("input:not([type='hidden']), textarea, select")
        : null;
      if (focusEl && typeof focusEl.focus === "function") {
        try {
          focusEl.focus({ preventScroll: true });
        } catch (err) {
          focusEl.focus();
        }
      }
      showToast(missing.length);
    },
    true
  );

  document.addEventListener("input", function (event) {
    var wrap = event.target && event.target.closest("[data-review-required='1']");
    if (wrap && fieldFilled(wrap)) clearMissing(wrap);
  });

  document.addEventListener("change", function (event) {
    var wrap = event.target && event.target.closest("[data-review-required='1']");
    if (wrap && fieldFilled(wrap)) clearMissing(wrap);
  });
})();
