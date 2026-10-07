(function () {
  var guidesEl = document.getElementById("ocr-field-guides");
  if (!guidesEl) return;

  var guides = {};
  try {
    guides = JSON.parse(guidesEl.textContent || "{}") || {};
  } catch (e) {
    guides = {};
  }

  var layer = null;
  var lastType = "";
  var lastIndex = -1;
  var aligned = {};
  var alignedKey = "";
  var fetchSeq = 0;

  function currentDocType() {
    var dt = document.getElementById("document_type");
    var value = ((dt && dt.value) || "").trim().toLowerCase();
    if (value === "birth" || value === "marriage" || value === "death") return value;
    return "";
  }

  function currentImageName() {
    var hid = document.getElementById("image_filename");
    return ((hid && hid.value) || "").trim();
  }

  function currentPct() {
    var el = document.getElementById("ocr-progress-text");
    var n = parseInt((el && el.textContent) || "0", 10);
    if (isNaN(n)) return 1;
    return Math.max(1, Math.min(100, n));
  }

  function host() {
    return document.getElementById("scan-wf-image-wrap");
  }

  function previewImage() {
    return document.getElementById("scan-wf-viewer-image");
  }

  function ensureLayer() {
    var wrap = host();
    if (!wrap) return null;
    if (!layer || layer.parentNode !== wrap) {
      layer = document.getElementById("ocr-doc-targets");
      if (!layer) {
        layer = document.createElement("div");
        layer.id = "ocr-doc-targets";
        layer.className = "ocr-doc-targets";
        layer.setAttribute("aria-hidden", "true");
      }
      wrap.appendChild(layer);
    }
    layoutLayer();
    return layer;
  }

  function layoutLayer() {
    var wrap = host();
    var img = previewImage();
    if (!wrap || !img || !layer) return;
    var wrapRect = wrap.getBoundingClientRect();
    var imgRect = img.getBoundingClientRect();
    if (imgRect.width < 8 || imgRect.height < 8 || wrapRect.width < 8) {
      layer.style.left = "0";
      layer.style.top = "0";
      layer.style.width = "100%";
      layer.style.height = "100%";
      return;
    }
    layer.style.left = Math.max(0, imgRect.left - wrapRect.left) + "px";
    layer.style.top = Math.max(0, imgRect.top - wrapRect.top) + "px";
    layer.style.width = imgRect.width + "px";
    layer.style.height = imgRect.height + "px";
    layer.style.right = "auto";
    layer.style.bottom = "auto";
  }

  function setLabel(text, countText) {
    var el = document.getElementById("ocr-doc-scan-label-text");
    if (el) el.textContent = text;
    var count = document.getElementById("ocr-doc-scan-label-count");
    if (!count) return;
    if (countText) {
      count.textContent = countText;
      count.classList.add("is-on");
    } else {
      count.textContent = "";
      count.classList.remove("is-on");
    }
  }

  function fieldsFor(type) {
    if (type && Array.isArray(aligned[type]) && aligned[type].length) {
      return aligned[type];
    }
    var list = guides[type];
    return Array.isArray(list) ? list : [];
  }

  function sourceKey(type) {
    return type + "|" + currentImageName();
  }

  function applyAligned(type, fields) {
    if (!type || !Array.isArray(fields) || !fields.length) return;
    aligned[type] = fields;
    lastType = "";
    lastIndex = -1;
    if (document.body.classList.contains("ocr-busy")) {
      paint(currentPct());
    }
  }

  function requestAligned(type) {
    if (!type) return;
    var key = sourceKey(type);
    if (key === alignedKey && aligned[type] && aligned[type].length) return;
    var url = guidesEl.getAttribute("data-align-url") || "/api/ocr/field-guides";
    var imageName = currentImageName();
    var query = "doc_type=" + encodeURIComponent(type);
    if (imageName) query += "&image=" + encodeURIComponent(imageName);
    var seq = ++fetchSeq;
    alignedKey = key;
    fetch(url + (url.indexOf("?") >= 0 ? "&" : "?") + query, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (data) {
        if (seq !== fetchSeq) return;
        if (!data || !data.ok || !Array.isArray(data.fields) || !data.fields.length) return;
        applyAligned(type, data.fields);
      })
      .catch(function () {});
  }

  function activeIndex(fields, pct) {
    if (!fields.length) return -1;
    if (pct < 18) return 0;
    var span = Math.max(1, fields.length);
    var t = (pct - 18) / 72;
    if (t < 0) t = 0;
    if (t > 0.999) t = 0.999;
    return Math.min(span - 1, Math.floor(t * span));
  }

  function render(type) {
    var node = ensureLayer();
    if (!node) return;
    var fields = fieldsFor(type);
    node.replaceChildren();
    var beam = document.createElement("span");
    beam.className = "ocr-doc-target-beam";
    node.appendChild(beam);
    fields.forEach(function (field, i) {
      var box = document.createElement("span");
      box.className = "ocr-doc-target";
      box.dataset.index = String(i);
      box.style.left = (field.left * 100).toFixed(2) + "%";
      box.style.top = (field.top * 100).toFixed(2) + "%";
      box.style.width = (field.width * 100).toFixed(2) + "%";
      box.style.height = (field.height * 100).toFixed(2) + "%";
      box.style.animationDelay = Math.min(i * 0.045, 0.7) + "s";
      var sweep = document.createElement("span");
      sweep.className = "ocr-doc-target-sweep";
      var tag = document.createElement("span");
      tag.className = "ocr-doc-target-tag";
      tag.textContent = field.key;
      box.appendChild(sweep);
      box.appendChild(tag);
      node.appendChild(box);
    });
    lastType = type;
    lastIndex = -1;
    layoutLayer();
  }

  function paint(pct) {
    if (!document.body.classList.contains("ocr-busy")) {
      stop();
      return;
    }
    var type = currentDocType();
    var node = ensureLayer();
    if (!node) return;
    if (!type) {
      node.hidden = true;
      setLabel("Identifying document");
      return;
    }
    requestAligned(type);
    if (type !== lastType || !node.querySelector(".ocr-doc-target")) render(type);
    else layoutLayer();
    var fields = fieldsFor(type);
    if (!fields.length) {
      node.hidden = true;
      setLabel("Reading document");
      return;
    }
    node.hidden = false;
    var idx = activeIndex(fields, pct);
    var countText = idx >= 0 ? idx + 1 + " / " + fields.length : "";
    if (idx !== lastIndex) {
      lastIndex = idx;
      Array.prototype.forEach.call(node.children, function (box) {
        if (!box.classList.contains("ocr-doc-target")) return;
        var fieldIndex = parseInt(box.dataset.index || "-1", 10);
        box.classList.toggle("is-done", fieldIndex < idx);
        box.classList.toggle("is-active", fieldIndex === idx);
        box.classList.toggle("is-upcoming", fieldIndex > idx);
      });
    }
    var current = fields[idx];
    setLabel(current ? "Reading " + current.key : "Reading document", countText);
  }

  function start() {
    lastType = "";
    lastIndex = -1;
    alignedKey = "";
    paint(currentPct());
  }

  function stop() {
    lastType = "";
    lastIndex = -1;
    if (layer) layer.hidden = true;
    setLabel("Reading document");
  }

  window.DaragaOcrTargets = {
    start: start,
    stop: stop,
    setProgress: paint,
    setType: function () {
      lastType = "";
      alignedKey = "";
      paint(currentPct());
    },
    layout: layoutLayer,
  };

  var section = document.getElementById("scan-wf-preview-viewer");
  if (section) {
    section.addEventListener("document-viewer-layout", layoutLayer);
  }
  var img = previewImage();
  if (img) {
    img.addEventListener("load", layoutLayer);
  }
  window.addEventListener("resize", layoutLayer);

  if (document.body.classList.contains("ocr-busy")) start();
})();
