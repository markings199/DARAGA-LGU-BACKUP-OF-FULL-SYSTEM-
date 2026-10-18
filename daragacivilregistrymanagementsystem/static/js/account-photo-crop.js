(function () {
  var MAX_PICK_BYTES = 8 * 1024 * 1024;
  var EXPORT_PX = 512;
  var ALLOWED = {
    "image/jpeg": 1,
    "image/png": 1,
    "image/webp": 1,
  };
  var picker = document.getElementById("account_photo_file");
  var cropped = document.getElementById("account_photo_cropped");
  var form = document.getElementById("account-photo-form");
  var overlay = document.getElementById("account-crop");
  var stage = document.getElementById("account-crop-stage");
  var image = document.getElementById("account-crop-image");
  var zoom = document.getElementById("account-crop-zoom");
  var saveBtn = document.getElementById("account-crop-save");
  var errEl = document.getElementById("account-crop-error");
  if (!picker || !cropped || !form || !overlay || !stage || !image || !zoom || !saveBtn) return;

  var objectUrl = "";
  var natW = 0;
  var natH = 0;
  var posX = 0;
  var posY = 0;
  var zoomVal = 1;
  var drag = null;

  function showError(msg) {
    if (!errEl) return;
    errEl.hidden = !msg;
    errEl.textContent = msg || "";
  }

  function revoke() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = "";
    }
  }

  function closeCrop() {
    overlay.hidden = true;
    document.body.classList.remove("account-crop-open");
    drag = null;
    picker.value = "";
    revoke();
    image.removeAttribute("src");
    showError("");
  }

  function clampPos() {
    var size = stage.clientWidth || 280;
    var minSide = Math.min(natW, natH) || 1;
    var drawW = (natW / minSide) * size * zoomVal;
    var drawH = (natH / minSide) * size * zoomVal;
    posX = Math.min(0, Math.max(size - drawW, posX));
    posY = Math.min(0, Math.max(size - drawH, posY));
    image.style.width = drawW + "px";
    image.style.height = drawH + "px";
    image.style.transform = "translate(" + posX + "px," + posY + "px)";
  }

  function fitImage() {
    var size = stage.clientWidth || 280;
    var minSide = Math.min(natW, natH) || 1;
    var drawW = (natW / minSide) * size;
    var drawH = (natH / minSide) * size;
    zoomVal = 1;
    zoom.value = "100";
    posX = (size - drawW) / 2;
    posY = (size - drawH) / 2;
    clampPos();
  }

  function openCrop() {
    overlay.hidden = false;
    document.body.classList.add("account-crop-open");
    showError("");
    requestAnimationFrame(fitImage);
  }

  function loadFile(file) {
    if (!file) return;
    var type = (file.type || "").toLowerCase();
    if (!ALLOWED[type]) {
      window.alert("Use a JPEG or PNG photo.");
      picker.value = "";
      return;
    }
    if (file.size > MAX_PICK_BYTES) {
      window.alert("Photo must be 8 MB or smaller.");
      picker.value = "";
      return;
    }
    var opts = { imageOrientation: "from-image" };
    var loader = window.createImageBitmap
      ? createImageBitmap(file, opts).catch(function () {
          return createImageBitmap(file);
        })
      : Promise.reject(new Error("bitmap"));
    loader
      .then(function (bmp) {
        if (bmp.width < 32 || bmp.height < 32) {
          bmp.close();
          throw new Error("small");
        }
        if (bmp.width * bmp.height > 25000000) {
          bmp.close();
          throw new Error("large");
        }
        var maxEdge = 1600;
        var scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
        var canvas = document.createElement("canvas");
        canvas.width = Math.max(32, Math.round(bmp.width * scale));
        canvas.height = Math.max(32, Math.round(bmp.height * scale));
        var ctx = canvas.getContext("2d");
        ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
        bmp.close();
        return new Promise(function (resolve, reject) {
          canvas.toBlob(
            function (blob) {
              if (!blob) reject(new Error("blob"));
              else resolve(blob);
            },
            "image/jpeg",
            0.92
          );
        });
      })
      .then(function (blob) {
        revoke();
        objectUrl = URL.createObjectURL(blob);
        image.onload = function () {
          natW = image.naturalWidth;
          natH = image.naturalHeight;
          openCrop();
        };
        image.onerror = function () {
          closeCrop();
          window.alert("Could not open that photo.");
        };
        image.src = objectUrl;
      })
      .catch(function () {
        picker.value = "";
        window.alert("Use a JPEG or PNG photo.");
      });
  }

  picker.addEventListener("change", function () {
    var file = picker.files && picker.files[0];
    loadFile(file);
  });

  zoom.addEventListener("input", function () {
    var size = stage.clientWidth || 280;
    var minSide = Math.min(natW, natH) || 1;
    var prevW = (natW / minSide) * size * zoomVal;
    var prevH = (natH / minSide) * size * zoomVal;
    var next = Math.max(1, Number(zoom.value) / 100);
    var cx = size / 2;
    var cy = size / 2;
    var relX = (cx - posX) / prevW;
    var relY = (cy - posY) / prevH;
    zoomVal = next;
    var drawW = (natW / minSide) * size * zoomVal;
    var drawH = (natH / minSide) * size * zoomVal;
    posX = cx - relX * drawW;
    posY = cy - relY * drawH;
    clampPos();
  });

  stage.addEventListener("pointerdown", function (ev) {
    if (overlay.hidden) return;
    drag = { id: ev.pointerId, x: ev.clientX - posX, y: ev.clientY - posY };
    stage.setPointerCapture(ev.pointerId);
    stage.classList.add("is-dragging");
    ev.preventDefault();
  });
  stage.addEventListener("pointermove", function (ev) {
    if (!drag || drag.id !== ev.pointerId) return;
    posX = ev.clientX - drag.x;
    posY = ev.clientY - drag.y;
    clampPos();
  });
  function endDrag(ev) {
    if (!drag || (ev && drag.id !== ev.pointerId)) return;
    drag = null;
    stage.classList.remove("is-dragging");
  }
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);

  overlay.addEventListener("click", function (ev) {
    if (ev.target && ev.target.getAttribute("data-crop-cancel") === "1") {
      closeCrop();
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !overlay.hidden) closeCrop();
  });

  saveBtn.addEventListener("click", function () {
    if (!natW || !natH) return;
    showError("");
    saveBtn.disabled = true;
    var size = stage.clientWidth || 280;
    var minSide = Math.min(natW, natH) || 1;
    var drawW = (natW / minSide) * size * zoomVal;
    var drawH = (natH / minSide) * size * zoomVal;
    var srcX = (-posX / drawW) * natW;
    var srcY = (-posY / drawH) * natH;
    var srcSize = (size / drawW) * natW;
    srcX = Math.max(0, Math.min(natW - srcSize, srcX));
    srcY = Math.max(0, Math.min(natH - srcSize, srcY));
    var canvas = document.createElement("canvas");
    canvas.width = EXPORT_PX;
    canvas.height = EXPORT_PX;
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(0, 0, EXPORT_PX, EXPORT_PX);
    ctx.drawImage(image, srcX, srcY, srcSize, srcSize, 0, 0, EXPORT_PX, EXPORT_PX);
    canvas.toBlob(
      function (blob) {
        saveBtn.disabled = false;
        if (!blob) {
          showError("Could not crop that photo.");
          return;
        }
        if (blob.size > 2 * 1024 * 1024) {
          showError("Photo must be 2 MB or smaller after crop.");
          return;
        }
        var out = new File([blob], "avatar.jpg", { type: "image/jpeg" });
        try {
          var dt = new DataTransfer();
          dt.items.add(out);
          cropped.files = dt.files;
        } catch (err) {
          showError("This browser cannot save the cropped photo.");
          return;
        }
        form.submit();
      },
      "image/jpeg",
      0.88
    );
  });
})();
