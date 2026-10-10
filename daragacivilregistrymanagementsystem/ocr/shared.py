import os
import threading
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
os.environ.setdefault("FLAGS_use_mkldnn", "1")
os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")

import cv2
import numpy as np

warnings.filterwarnings("ignore", message="No ccache found")
warnings.filterwarnings("ignore", category=UserWarning, module="paddle")


class OCREngineNotFoundError(Exception):
    pass


# Longest side sent to the recognizer. Certificate scans are often 2000px+;
# running the full resolution on CPU is the main reason OCR felt stuck.
_OCR_MAX_SIDE = 960

_OCR_INSTANCE = None
_OCR_WARMED = False
_OCR_LOCK = threading.Lock()
_PaddleOCR = None


@contextmanager
def _quiet_stdio():
    """Silence Python prints and native Windows tools (e.g. `where`) spawned by Paddle."""
    import sys

    stdout = getattr(sys.stdout, "_wrapped", sys.stdout)
    stderr = getattr(sys.stderr, "_wrapped", sys.stderr)
    if hasattr(stdout, "flush"):
        stdout.flush()
    if hasattr(stderr, "flush"):
        stderr.flush()

    saved_out = saved_err = None
    nul_fd = None
    try:
        saved_out = os.dup(1)
        saved_err = os.dup(2)
        nul_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(nul_fd, 1)
        os.dup2(nul_fd, 2)
        with open(os.devnull, "w") as devnull:
            with redirect_stdout(devnull), redirect_stderr(devnull):
                yield
    except OSError:
        with open(os.devnull, "w") as devnull:
            with redirect_stdout(devnull), redirect_stderr(devnull):
                yield
    finally:
        if saved_out is not None:
            os.dup2(saved_out, 1)
            os.close(saved_out)
        if saved_err is not None:
            os.dup2(saved_err, 2)
            os.close(saved_err)
        if nul_fd is not None:
            os.close(nul_fd)


def _load_paddleocr():
    global _PaddleOCR
    if _PaddleOCR is not None:
        return _PaddleOCR
    with _quiet_stdio():
        from paddleocr import PaddleOCR as _Cls
    _PaddleOCR = _Cls
    return _PaddleOCR


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
    try:
        cpu_threads = max(1, min(8, int(os.environ.get("OCR_CPU_THREADS") or "4")))
    except ValueError:
        cpu_threads = 4
    ocr_kwargs = dict(
        lang="en",
        ocr_version="PP-OCRv4",
        use_textline_orientation=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        text_det_limit_side_len=_OCR_MAX_SIDE,
        text_det_limit_type="max",
        text_recognition_batch_size=48,
        cpu_threads=cpu_threads,
        enable_mkldnn=True,
        show_log=False,
    )
    try:
        import paddle

        if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            ocr_kwargs["device"] = "gpu:0"
    except Exception:
        pass
    try:
        with _quiet_stdio():
            try:
                _OCR_INSTANCE = PaddleOCR(**ocr_kwargs)
            except (TypeError, ValueError):
                ocr_kwargs.pop("enable_mkldnn", None)
                ocr_kwargs.pop("show_log", None)
                ocr_kwargs.pop("device", None)
                try:
                    _OCR_INSTANCE = PaddleOCR(**ocr_kwargs)
                except (TypeError, ValueError):
                    ocr_kwargs.pop("cpu_threads", None)
                    ocr_kwargs.pop("ocr_version", None)
                    ocr_kwargs.pop("show_log", None)
                    ocr_kwargs.pop("device", None)
                    try:
                        _OCR_INSTANCE = PaddleOCR(**ocr_kwargs)
                    except TypeError:
                        _OCR_INSTANCE = PaddleOCR(
                            lang="en",
                            use_textline_orientation=False,
                            use_doc_orientation_classify=False,
                            use_doc_unwarping=False,
                        )
    except Exception as exc:
        raise OCREngineNotFoundError(
            "Failed to initialize PaddleOCR engine. Ensure 'paddlepaddle' is installed and compatible "
            "with this Python version."
        ) from exc
    return _OCR_INSTANCE


def warmup_ocr() -> None:
    """Compile CPU kernels once so the first real certificate scan is not the slow one."""
    global _OCR_WARMED
    if _OCR_WARMED:
        return
    img = np.full((48, 240, 3), 255, dtype=np.uint8)
    cv2.putText(img, "OCR", (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    with _quiet_stdio():
        run_ocr_on_image(img)
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


def _resize_for_ocr(img, max_side=None):
    limit = int(max_side or _OCR_MAX_SIDE)
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= limit:
        return img, 1.0
    scale = limit / float(longest)
    new_w = max(32, int(round(w * scale)))
    new_h = max(32, int(round(h * scale)))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, scale


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


def run_ocr_on_image(img, max_side=None):
    """
    Run PaddleOCR on an in-memory image (numpy array, BGR).
    Returns the same "dict per page" structure as run_ocr_on_image_path.
    Boxes are returned in the original image coordinates.
    """
    if img is None:
        raise ValueError("Image is None")
    ocr_img, scale = _resize_for_ocr(img, max_side=max_side)
    with _OCR_LOCK:
        ocr = get_ocr()
        if hasattr(ocr, "predict"):
            raw = ocr.predict(ocr_img)
        else:
            raw = ocr.ocr(ocr_img, cls=False)
        if hasattr(ocr, "predict"):
            pages = _normalize_ocr_pages(raw)
            return _scale_pages_to_original(pages, scale)
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
    return _scale_pages_to_original(pages, scale)


def run_ocr_on_image_path(img_path: str):
    """
    Read an image from disk and run PaddleOCR.

    Newer PaddleOCR versions expose .predict(), older ones
    only expose .ocr(). This helper normalizes both into
    the "dict per page" structure expected by the extractors.
    """
    img = cv2.imread(img_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {img_path}")
    return run_ocr_on_image(img)
