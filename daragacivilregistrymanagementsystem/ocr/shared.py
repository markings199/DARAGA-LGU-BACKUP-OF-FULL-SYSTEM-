import os
import threading
import time
import warnings
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from importlib.util import find_spec

os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("GLOG_v", "0")
os.environ.setdefault("FLAGS_minloglevel", "3")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("KMP_WARNINGS", "0")
os.environ.setdefault("OMP_DISPLAY_ENV", "FALSE")
_cpu_n = os.cpu_count() or 4
_ocr_threads = os.environ.get("OCR_CPU_THREADS") or ("2" if _cpu_n >= 4 else "1")
os.environ.setdefault("OCR_CPU_THREADS", _ocr_threads)
os.environ.setdefault("OMP_NUM_THREADS", _ocr_threads)
os.environ.setdefault("MKL_NUM_THREADS", _ocr_threads)
os.environ.setdefault("OPENBLAS_NUM_THREADS", _ocr_threads)
os.environ.setdefault("NUMEXPR_NUM_THREADS", _ocr_threads)
os.environ.setdefault("FLAGS_omp_num_threads", _ocr_threads)
os.environ.setdefault("PADDLE_NUM_THREADS", _ocr_threads)
os.environ.setdefault("FLAGS_use_mkldnn", "1")
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
os.environ.setdefault("FLAGS_conv_workspace_size_limit", "4096")
os.environ.setdefault("FLAGS_eager_delete_tensor_gb", "0.0")
os.environ.setdefault("FLAGS_fast_eager_deletion_mode", "1")
os.environ.setdefault("KMP_BLOCKTIME", "0")

import cv2
import numpy as np

try:
    cv2.setUseOptimized(True)
except Exception:
    pass

warnings.filterwarnings("ignore", message="No ccache found")
warnings.filterwarnings("ignore", category=UserWarning, module="paddle")


class OCREngineNotFoundError(Exception):
    pass


def _ocr_max_side() -> int:
    """Longest side sent to PaddleOCR. 736 is the PP-OCRv4 detector size and targets a 20–25s page read."""
    try:
        value = int(os.environ.get("OCR_MAX_SIDE") or "736")
    except ValueError:
        value = 736
    return max(720, min(960, value))


_OCR_MAX_SIDE = _ocr_max_side()

_OCR_INSTANCE = None
_OCR_WARMED = False
_OCR_LOCK = threading.Lock()
_PaddleOCR = None


def _safe_flush(stream) -> None:
    try:
        if stream is None or getattr(stream, "closed", False):
            return
        if hasattr(stream, "flush"):
            stream.flush()
    except (ValueError, OSError, AttributeError):
        pass


@contextmanager
def _quiet_stdio():
    """Silence Python prints and native Windows tools (e.g. `where`) spawned by Paddle."""
    import sys

    stdout = getattr(sys.stdout, "_wrapped", sys.stdout)
    stderr = getattr(sys.stderr, "_wrapped", sys.stderr)
    _safe_flush(stdout)
    _safe_flush(stderr)

    saved_out = saved_err = None
    nul_fd = None
    try:
        try:
            saved_out = os.dup(1)
            saved_err = os.dup(2)
            nul_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(nul_fd, 1)
            os.dup2(nul_fd, 2)
        except OSError:
            saved_out = saved_err = None
            if nul_fd is not None:
                try:
                    os.close(nul_fd)
                except OSError:
                    pass
                nul_fd = None
        with open(os.devnull, "w") as devnull:
            with redirect_stdout(devnull), redirect_stderr(devnull):
                yield
    except (OSError, ValueError):
        yield
    finally:
        if saved_out is not None:
            try:
                os.dup2(saved_out, 1)
                os.close(saved_out)
            except OSError:
                pass
        if saved_err is not None:
            try:
                os.dup2(saved_err, 2)
                os.close(saved_err)
            except OSError:
                pass
        if nul_fd is not None:
            try:
                os.close(nul_fd)
            except OSError:
                pass


def _load_paddleocr():
    global _PaddleOCR
    if _PaddleOCR is not None:
        return _PaddleOCR
    with _quiet_stdio():
        from paddleocr import PaddleOCR as _Cls
    _PaddleOCR = _Cls
    return _PaddleOCR


def _unknown_kwarg_name(exc: BaseException) -> str:
    """PaddleOCR 2.x raises TypeError; 3.x raises ValueError('Unknown argument: show_log')."""
    message = str(exc or "")
    if "Unknown argument:" in message:
        return message.split("Unknown argument:", 1)[1].strip().strip("'\" .,")
    marker = "unexpected keyword argument "
    if marker in message:
        tail = message.split(marker, 1)[1].strip()
        return tail.split()[0].strip("'\"") if tail else ""
    return ""


def _create_paddleocr(PaddleOCR, kwargs: dict):
    pending = dict(kwargs)
    last_error = None
    while True:
        try:
            return PaddleOCR(**pending)
        except (TypeError, ValueError) as exc:
            last_error = exc
            name = _unknown_kwarg_name(exc)
            if name and name in pending:
                pending.pop(name, None)
                continue
            raise
        except Exception:
            if last_error is not None:
                raise last_error
            raise


def get_ocr():
    """
    Lazily create a shared PaddleOCR instance.
    """
    global _OCR_INSTANCE
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE

    if find_spec("paddle") is None:
        raise OCREngineNotFoundError(
            "PaddleOCR requires the 'paddlepaddle' package. Install it in the project environment: "
            "pip install paddlepaddle"
        )

    PaddleOCR = _load_paddleocr()
    cpu_n = os.cpu_count() or 4
    try:
        requested = int(os.environ.get("OCR_CPU_THREADS") or "10")
    except ValueError:
        requested = 10
    # Dedicated OCR process may use every core. Flask jobs stay at their env cap (usually 2).
    if os.environ.get("OCR_WORKER", "").strip() == "1":
        cap = max(1, min(16, cpu_n))
    elif cpu_n <= 2:
        cap = 1
    elif cpu_n <= 4:
        cap = max(2, cpu_n - 1)
    else:
        cap = min(10, cpu_n - 1)
    cpu_threads = max(1, min(cap, requested))
    max_side = _ocr_max_side()
    try:
        rec_batch = max(8, min(64, int(os.environ.get("OCR_REC_BATCH") or "64")))
    except ValueError:
        rec_batch = 48
    use_gpu = False
    try:
        import paddle

        use_gpu = bool(paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
    except Exception:
        use_gpu = False
    # PaddleOCR 2.7 (this office install) uses det_limit_side_len / rec_batch_num / use_angle_cls.
    # PaddleOCR 3.x uses the text_* names. Try 2.7 first so MKLDNN and the size cap actually apply.
    kwargs_v2 = dict(
        lang="en",
        ocr_version="PP-OCRv4",
        use_angle_cls=False,
        use_gpu=use_gpu,
        enable_mkldnn=not use_gpu,
        cpu_threads=cpu_threads,
        det_limit_side_len=max_side,
        det_limit_type="max",
        rec_batch_num=rec_batch,
        drop_score=0.45,
        det_db_score_mode="fast",
        ir_optim=True,
        use_mp=False,
        total_process_num=1,
        show_log=False,
    )
    kwargs_v3 = dict(
        lang="en",
        ocr_version="PP-OCRv4",
        use_textline_orientation=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        text_det_limit_side_len=max_side,
        text_det_limit_type="max",
        text_recognition_batch_size=rec_batch,
        cpu_threads=cpu_threads,
        enable_mkldnn=not use_gpu,
        show_log=False,
    )
    if use_gpu:
        kwargs_v3["device"] = "gpu:0"
    last_error = None
    try:
        with _quiet_stdio():
            for kwargs in (
                kwargs_v2,
                kwargs_v3,
                dict(lang="en", use_textline_orientation=False),
                dict(lang="en", use_angle_cls=False, show_log=False),
                dict(lang="en"),
            ):
                try:
                    _OCR_INSTANCE = _create_paddleocr(PaddleOCR, kwargs)
                    return _OCR_INSTANCE
                except (TypeError, ValueError) as exc:
                    last_error = exc
                    continue
    except Exception as exc:
        raise OCREngineNotFoundError(
            "Failed to initialize PaddleOCR engine. Ensure 'paddlepaddle' is installed and compatible "
            "with this Python version."
        ) from (exc if last_error is None else last_error)
    if _OCR_INSTANCE is None:
        raise OCREngineNotFoundError(
            "Failed to initialize PaddleOCR engine. Ensure 'paddlepaddle' is installed and compatible "
            "with this Python version."
        ) from last_error
    return _OCR_INSTANCE


def warmup_ocr(abort_event=None, blocking: bool = False) -> None:
    """Compile CPU kernels on a dummy page. Same models and 736 cap as live OCR."""
    global _OCR_WARMED
    if _OCR_WARMED:
        return
    if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
        return
    acquired = _OCR_LOCK.acquire(blocking=True, timeout=45) if blocking else _OCR_LOCK.acquire(blocking=False)
    if not acquired:
        return
    try:
        if _OCR_WARMED:
            return
        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
            return
        _warmup_ocr_locked()
    finally:
        _OCR_LOCK.release()


def trim_scanner_bed(img):
    """Drop empty scanner lid only. The certificate itself stays complete."""
    if img is None or getattr(img, "size", 0) == 0:
        return img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    height, width = gray.shape[:2]
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    pts = cv2.findNonZero(mask)
    if pts is None:
        return img
    x, y, bw, bh = cv2.boundingRect(pts)
    pad = 12
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(width, x + bw + pad)
    y1 = min(height, y + bh + pad)
    if (x1 - x0) < int(0.82 * width) or (y1 - y0) < int(0.82 * height):
        return img
    if x0 <= 2 and y0 <= 2 and x1 >= width - 2 and y1 >= height - 2:
        return img
    return img[y0:y1, x0:x1]


def _warmup_sample_images():
    """Official PSA pages (marriage first — densest). Same 736 cap as live OCR."""
    here = os.path.dirname(os.path.abspath(__file__))
    names = (
        os.path.join(here, "templates", "marriage_form97.jpg"),
        os.path.join(here, "title_templates", "source", "marriage.jpg"),
        os.path.join(here, "templates", "birth_form102.jpg"),
        os.path.join(here, "templates", "death_form103.jpg"),
    )
    seen = set()
    images = []
    for path in names:
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        img = imread_bgr(path)
        if img is None or getattr(img, "size", 0) == 0:
            continue
        images.append(trim_scanner_bed(img))
        # One official marriage page is enough: same PP-OCRv4 kernels, faster ready.
        if images:
            break
    return images


def _warmup_dummy_page():
    side = _ocr_max_side()
    height = side - (side % 32)
    width = max(320, (int(round(side * 0.76)) // 32) * 32)
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.rectangle(img, (8, 8), (width - 8, height - 8), (0, 0, 0), 2)
    lines = (
        "CERTIFICATE OF LIVE BIRTH",
        "Registry No. 2026-000123",
        "JUAN MIGUEL SANTOS",
        "15 MARCH 1995 MALE",
        "DARAGA COMMUNITY HOSPITAL ALBAY",
        "MARIA SANTOS CRUZ FILIPINO",
        "16 JUNE 1995 MUNICIPAL CIVIL REGISTRAR",
    )
    y = 40
    for line in lines:
        cv2.putText(img, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)
        y += 36
    return img


def _warmup_shape_pages():
    """Compile MKLDNN on the official birth/death/marriage pages at the live 736 cap."""
    here = os.path.dirname(os.path.abspath(__file__))
    names = (
        os.path.join(here, "templates", "birth_form102.jpg"),
        os.path.join(here, "templates", "death_form103.jpg"),
        os.path.join(here, "templates", "marriage_form97.jpg"),
    )
    pages = []
    for path in names:
        if not os.path.isfile(path):
            continue
        img = imread_bgr(path)
        if img is None or getattr(img, "size", 0) == 0:
            continue
        pages.append(_resize_for_ocr(trim_scanner_bed(img), max_side=_ocr_max_side())[0])
    if pages:
        return pages
    samples = _warmup_sample_images()
    if samples:
        return [_resize_for_ocr(samples[0], max_side=_ocr_max_side())[0]]
    return [_warmup_dummy_page()]


def _warmup_ocr_locked() -> None:
    global _OCR_WARMED
    if _OCR_WARMED:
        return
    pages = _warmup_shape_pages()
    ran = False
    for img in pages:
        try:
            with _quiet_stdio():
                _run_ocr_unlocked(img)
            ran = True
        except Exception:
            try:
                _run_ocr_unlocked(img)
                ran = True
            except Exception:
                continue
    if ran:
        _OCR_WARMED = True


def _page_to_dict(page):
    if isinstance(page, dict) and "rec_texts" in page:
        return page
    getter = page.get if hasattr(page, "get") else None
    if getter is None:
        return {"rec_texts": [], "rec_scores": [], "dt_polys": []}
    return {
        "rec_texts": list(getter("rec_texts") or []),
        "rec_scores": list(getter("rec_scores") or []),
        "dt_polys": list(getter("dt_polys") or []),
    }


def _normalize_ocr_pages(raw):
    if raw is None:
        return []
    if isinstance(raw, dict) or hasattr(raw, "get"):
        return [_page_to_dict(raw)]
    pages = []
    for page in raw:
        pages.append(_page_to_dict(page))
    return pages


def imread_bgr(path: str):
    """Fast Unicode-safe BGR load (Windows paths). Same pixels as cv2.imread."""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _resize_for_ocr(img, max_side=None):
    limit = int(max_side or _ocr_max_side())
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= limit:
        return np.ascontiguousarray(img), 1.0
    scale = limit / float(longest)
    new_w = max(32, int(round(w * scale)))
    new_h = max(32, int(round(h * scale)))
    new_w -= new_w % 32
    new_h -= new_h % 32
    new_w = max(32, new_w)
    new_h = max(32, new_h)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(resized), scale


def _scale_pages_to_original(pages, scale):
    if scale == 1.0 or not pages:
        return pages
    inv = 1.0 / scale
    for page in pages:
        polys = page.get("dt_polys") or []
        scaled = []
        for box in polys:
            arr = np.asarray(box, dtype=np.float32) * inv
            scaled.append(arr)
        page["dt_polys"] = scaled
    return pages


def _texts_from_rec_only(raw) -> list:
    texts = []
    if raw is None:
        return texts
    rows = raw
    if isinstance(raw, list) and raw and not isinstance(raw[0], (list, tuple)):
        rows = [raw]
    for row in rows or []:
        if isinstance(row, str):
            if row.strip():
                texts.append(row.strip())
            continue
        if not row:
            continue
        if isinstance(row, (list, tuple)) and len(row) == 2 and isinstance(row[0], str):
            text = str(row[0] or "").strip()
            if text:
                texts.append(text)
            continue
        for item in row:
            if isinstance(item, str):
                if item.strip():
                    texts.append(item.strip())
                continue
            if not item:
                continue
            if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], (list, tuple)):
                text = str(item[1][0] or "").strip()
            elif isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                text = str(item[0] or "").strip()
            else:
                text = ""
            if text:
                texts.append(text)
    return texts


def _looks_like_dict_pages(raw) -> bool:
    if raw is None:
        return False
    if isinstance(raw, dict):
        return True
    if isinstance(raw, list) and raw and (isinstance(raw[0], dict) or hasattr(raw[0], "get")):
        return True
    return hasattr(raw, "get") and not isinstance(raw, (list, tuple))


def _pages_from_line_lists(raw):
    pages = []
    for page in raw or []:
        rec_texts, rec_scores, dt_polys = [], [], []
        for line in page or []:
            if not line or len(line) != 2:
                continue
            box, (text, score) = line
            rec_texts.append(str(text))
            rec_scores.append(float(score))
            dt_polys.append(box)
        pages.append({"rec_texts": rec_texts, "rec_scores": rec_scores, "dt_polys": dt_polys})
    return pages


def _pages_from_raw(raw):
    if _looks_like_dict_pages(raw):
        return _normalize_ocr_pages(raw)
    return _pages_from_line_lists(raw)


def _paddle_page_raw(ocr, ocr_img):
    """Prefer classic .ocr() (det+rec only). .predict() can run extra PaddleX document models."""
    if hasattr(ocr, "ocr"):
        try:
            return ocr.ocr(ocr_img, det=True, rec=True, cls=False)
        except TypeError:
            try:
                return ocr.ocr(ocr_img, cls=False)
            except TypeError:
                try:
                    return ocr.ocr(ocr_img)
                except TypeError:
                    pass
    if hasattr(ocr, "predict"):
        return ocr.predict(ocr_img)
    raise RuntimeError("PaddleOCR has neither ocr() nor predict()")


def _recognize_crop_unlocked(ocr, img, max_side=400) -> str:
    if img is None or getattr(img, "size", 0) == 0:
        return ""
    ocr_img, _scale = _resize_for_ocr(img, max_side=max_side)
    if hasattr(ocr, "ocr"):
        try:
            raw = ocr.ocr(ocr_img, det=False, rec=True, cls=False)
            texts = _texts_from_rec_only(raw)
            if texts:
                return " ".join(texts).strip()
        except TypeError:
            try:
                raw = ocr.ocr(ocr_img, det=False, cls=False)
                texts = _texts_from_rec_only(raw)
                if texts:
                    return " ".join(texts).strip()
            except TypeError:
                pass
    return ""


def run_ocr_text_only(img, max_side=400) -> str:
    """Recognize text in a pre-cropped cell. Recognition-only — never a second full-page detect."""
    with _OCR_LOCK:
        return _recognize_crop_unlocked(get_ocr(), img, max_side=max_side)


def run_ocr_text_only_many(images, max_side=480) -> list:
    """Recognize many write-in cells under one lock. Same rec model as a full page."""
    if not images:
        return []
    with _OCR_LOCK:
        ocr = get_ocr()
        return [_recognize_crop_unlocked(ocr, img, max_side=max_side) for img in images]


def _run_ocr_unlocked(img, max_side=None):
    if img is None:
        raise ValueError("Image is None")
    started = time.time()
    ocr_img, scale = _resize_for_ocr(img, max_side=max_side)
    ocr = get_ocr()
    raw = _paddle_page_raw(ocr, ocr_img)
    pages = _scale_pages_to_original(_pages_from_raw(raw), scale)
    print(
        f"Paddle page {ocr_img.shape[1]}x{ocr_img.shape[0]} in {time.time() - started:.1f}s "
        f"threads={os.environ.get('OCR_CPU_THREADS') or '?'}",
        flush=True,
    )
    return pages


def run_ocr_on_image(img, max_side=None):
    """
    Run PaddleOCR on an in-memory image (numpy array, BGR).
    Returns the same "dict per page" structure as run_ocr_on_image_path.
    Boxes are returned in the original image coordinates.
    """
    with _OCR_LOCK:
        return _run_ocr_unlocked(img, max_side=max_side)


def run_ocr_on_image_path(img_path: str):
    """
    Read an image from disk and run PaddleOCR.

    Newer PaddleOCR versions expose .predict(), older ones
    only expose .ocr(). This helper normalizes both into
    the "dict per page" structure expected by the extractors.
    """
    img = imread_bgr(img_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {img_path}")
    return run_ocr_on_image(img)
