"""OCR runs in a separate process so Flask can keep serving pages."""
from __future__ import annotations

import os
import sys
import threading
import traceback
from multiprocessing import get_context
from queue import Empty, Full
from typing import Optional


_CTX = get_context("spawn")
_IN = None
_OUT = None
_PROC = None


def _worker_thread_count() -> str:
    """Use most cores in the OCR process; leave a couple for Flask and the browser."""
    n = os.cpu_count() or 4
    if n <= 2:
        return "1"
    if n <= 4:
        return str(n - 1)
    return str(min(8, n - 1))


def limit_ocr_cpu_env(force: bool = False, worker: bool = False) -> None:
    threads = _worker_thread_count() if worker else (os.environ.get("OCR_CPU_THREADS") or "2")
    keys = (
        "OCR_CPU_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "FLAGS_omp_num_threads",
        "PADDLE_NUM_THREADS",
    )
    for key in keys:
        if force or worker:
            os.environ[key] = threads
        else:
            os.environ.setdefault(key, threads)


def set_below_normal_priority() -> None:
    """Optional: slightly lower only the current thread, not the whole process."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        THREAD_PRIORITY_BELOW_NORMAL = -1
        ctypes.windll.kernel32.SetThreadPriority(
            ctypes.windll.kernel32.GetCurrentThread(),
            THREAD_PRIORITY_BELOW_NORMAL,
        )
    except Exception:
        pass


def run_worker(in_q, out_q) -> None:
    # Do not inherit Flask's 2-thread cap; this process exists to run OCR quickly.
    limit_ocr_cpu_env(force=True, worker=True)
    try:
        import cv2

        cv2.setUseOptimized(True)
        try:
            cv2.setNumThreads(max(1, int(os.environ.get("OCR_CPU_THREADS") or "4")))
        except Exception:
            pass
        from ocr.birth import extract_birth_data
        from ocr.death import extract_death_data
        from ocr.detect import detect_from_image
        from ocr.marriage import extract_marriage_data
        from ocr.shared import get_ocr, warmup_ocr
    except Exception:
        out_q.put({"type": "boot_error", "error": traceback.format_exc()})
        return

    # Accept jobs immediately. Load Paddle in the background so the first scan is not waiting on import.
    out_q.put({"type": "ready"})
    engine_ready = False

    def _preload_engine():
        nonlocal engine_ready
        try:
            get_ocr()
            warmup_ocr()
            engine_ready = True
        except Exception:
            pass

    threading.Thread(target=_preload_engine, daemon=True, name="ocr-preload").start()

    while True:
        try:
            job = in_q.get()
        except (EOFError, OSError, KeyboardInterrupt):
            break
        if not job or job.get("type") == "stop":
            break
        job_id = job.get("job_id") or ""
        job_type = (job.get("type") or "run").strip().lower()
        doc_type = (job.get("doc_type") or "").strip().lower()
        path = job.get("path") or ""
        image_filename = job.get("image_filename") or ""
        try:
            if not engine_ready:
                if job_type != "detect":
                    out_q.put(
                        {
                            "type": "progress",
                            "job_id": job_id,
                            "progress": 18,
                            "message": "Loading OCR engine...",
                        }
                    )
                get_ocr()
                warmup_ocr()
                engine_ready = True
            if job_type == "detect":
                result = detect_from_image(path)
                out_q.put(
                    {
                        "type": "detect_done",
                        "job_id": job_id,
                        "doc_type": result.get("doc_type"),
                        "label": result.get("label") or "",
                        "confidence": result.get("confidence") or "none",
                        "reason": result.get("reason") or "",
                    }
                )
                continue
            if doc_type not in ("birth", "marriage", "death"):
                out_q.put(
                    {
                        "type": "progress",
                        "job_id": job_id,
                        "progress": 28,
                        "message": "Identifying document type...",
                    }
                )
                detected = detect_from_image(path)
                doc_type = (detected.get("doc_type") or "").strip().lower()
                if doc_type not in ("birth", "marriage", "death"):
                    out_q.put(
                        {
                            "type": "error",
                            "job_id": job_id,
                            "error": (
                                "Could not identify whether this is a birth, marriage, "
                                "or death certificate. Choose the type and run OCR again."
                            ),
                        }
                    )
                    continue
            out_q.put(
                {
                    "type": "progress",
                    "job_id": job_id,
                    "progress": 35,
                    "message": "Reading the certificate...",
                }
            )
            if doc_type == "birth":
                out_q.put(
                    {
                        "type": "progress",
                        "job_id": job_id,
                        "progress": 50,
                        "message": "Extracting birth fields...",
                    }
                )
                data = extract_birth_data(path)
            elif doc_type == "marriage":
                out_q.put(
                    {
                        "type": "progress",
                        "job_id": job_id,
                        "progress": 50,
                        "message": "Extracting marriage fields...",
                    }
                )
                data = extract_marriage_data(path)
            else:
                out_q.put(
                    {
                        "type": "progress",
                        "job_id": job_id,
                        "progress": 50,
                        "message": "Extracting death fields...",
                    }
                )
                data = extract_death_data(path)
            out_q.put(
                {
                    "type": "done",
                    "job_id": job_id,
                    "doc_type": doc_type,
                    "image_filename": image_filename,
                    "data": data,
                }
            )
        except Exception:
            if job_type == "detect":
                out_q.put(
                    {
                        "type": "detect_done",
                        "job_id": job_id,
                        "doc_type": None,
                        "label": "",
                        "confidence": "none",
                        "reason": "Could not identify the document type",
                    }
                )
            else:
                out_q.put(
                    {
                        "type": "error",
                        "job_id": job_id,
                        "error": traceback.format_exc(),
                    }
                )


def result_queue():
    return _OUT


def worker_is_alive() -> bool:
    return bool(_PROC is not None and _PROC.is_alive())


def start_ocr_worker() -> bool:
    global _IN, _OUT, _PROC
    if worker_is_alive():
        return True
    stop_ocr_worker()
    try:
        _IN = _CTX.Queue(maxsize=4)
        _OUT = _CTX.Queue(maxsize=16)
        _PROC = _CTX.Process(
            target=run_worker,
            args=(_IN, _OUT),
            name="daraga-ocr-worker",
            daemon=True,
        )
        _PROC.start()
        return worker_is_alive()
    except Exception:
        _IN = _OUT = _PROC = None
        return False


def submit_ocr_job(job_id: str, doc_type: str, path: str, image_filename: str) -> bool:
    if not worker_is_alive() or _IN is None:
        return False
    try:
        _IN.put_nowait(
            {
                "type": "run",
                "job_id": job_id,
                "doc_type": doc_type,
                "path": path,
                "image_filename": image_filename,
            }
        )
        return True
    except Full:
        return False


def submit_detect_job(job_id: str, path: str) -> bool:
    if not worker_is_alive() or _IN is None:
        return False
    try:
        _IN.put_nowait(
            {
                "type": "detect",
                "job_id": job_id,
                "path": path,
            }
        )
        return True
    except Full:
        return False


def stop_ocr_worker() -> None:
    global _IN, _OUT, _PROC
    proc = _PROC
    in_q = _IN
    _PROC = None
    _IN = None
    _OUT = None
    if in_q is not None:
        try:
            in_q.put_nowait({"type": "stop"})
        except Exception:
            pass
    if proc is not None and proc.is_alive():
        proc.join(timeout=0.4)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=1.0)


def get_result(timeout: float = 0.35) -> Optional[dict]:
    q = _OUT
    if q is None:
        return None
    try:
        return q.get(timeout=timeout)
    except (Empty, EOFError, OSError):
        return None
