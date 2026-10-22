(function () {
  function attr(root, name) {
    return (root.getAttribute(name) || "").trim();
  }

  function fieldVal(scope, name) {
    var el = scope.querySelector('[name="' + name + '"]');
    return el && el.value ? String(el.value).trim() : "";
  }

  function buildLegitimation(root) {
    var form = root.closest("form") || document;
    var father = fieldVal(form, "Name of Father") || attr(root, "data-father");
    var mother = fieldVal(form, "Name of Mother") || attr(root, "data-mother");
    var child =
      fieldVal(root, "annotation_new_name") ||
      fieldVal(form, "Name of Child") ||
      attr(root, "data-child");
    var marriageDate =
      fieldVal(form, "Date of Marriage of Parents") ||
      fieldVal(form, "Date of Marriage") ||
      attr(root, "data-marriage-date");
    var marriagePlace =
      fieldVal(form, "Place of Marriage of Parents") ||
      fieldVal(form, "Place of Marriage") ||
      attr(root, "data-marriage-place");
    var registry = fieldVal(root, "annotation_marriage_registry");

    var parts = ["LEGITIMATED BY SUBSEQUENT MARRIAGE OF PARENTS"];
    if (father && mother) parts.push(father + " AND " + mother);
    else if (father || mother) parts.push(father || mother);
    if (marriageDate) parts.push("ON " + marriageDate);
    if (marriagePlace) parts.push("AT " + marriagePlace);
    if (registry) parts.push("UNDER REGISTRY NO. " + registry);
    var sentence = parts.join(" ");
    if (child) sentence += ", THE CHILD SHALL BE KNOWN AS " + child;
    sentence = sentence.replace(/\s+/g, " ").trim();
    if (sentence && sentence.charAt(sentence.length - 1) !== ".") sentence += ".";
    return sentence.toUpperCase();
  }

  function syncStamps(root) {
    var textEl = root.querySelector('[name="annotation_text"]');
    var signEl = root.querySelector('[name="annotation_signatory"]');
    var titleEl = root.querySelector('[name="annotation_title"]');
    var text = textEl && textEl.value ? String(textEl.value).trim() : "";
    var signatory = signEl && signEl.value ? String(signEl.value).trim() : "";
    var title = titleEl && titleEl.value ? String(titleEl.value).trim() : "";
    var stamps = document.querySelectorAll("[data-annotation-stamp]");
    for (var i = 0; i < stamps.length; i += 1) {
      var stamp = stamps[i];
      var textNode = stamp.querySelector(".doc-annotation-text");
      var signNode = stamp.querySelector(".doc-annotation-signatory");
      var titleNode = stamp.querySelector(".doc-annotation-title");
      if (textNode) textNode.textContent = text;
      if (signNode) signNode.textContent = signatory;
      if (titleNode) titleNode.textContent = title;
      if (text) stamp.classList.remove("is-empty");
      else stamp.classList.add("is-empty");
    }
  }

  function bind(root) {
    var fillBtn = root.querySelector("[data-annotation-fill]");
    var textEl = root.querySelector('[name="annotation_text"]');
    if (fillBtn && textEl) {
      fillBtn.addEventListener("click", function () {
        textEl.value = buildLegitimation(root);
        syncStamps(root);
      });
    }
    var inputs = root.querySelectorAll(
      '[name="annotation_text"], [name="annotation_signatory"], [name="annotation_title"]'
    );
    for (var i = 0; i < inputs.length; i += 1) {
      inputs[i].addEventListener("input", function () {
        syncStamps(root);
      });
    }
  }

  function initAnnotation() {
    document.querySelectorAll("[data-annotation-form]").forEach(function (root) {
      if (root.getAttribute("data-annotation-bound")) return;
      root.setAttribute("data-annotation-bound", "1");
      bind(root);
    });
  }

  initAnnotation();
  window.addEventListener("cr:page", initAnnotation);
})();
