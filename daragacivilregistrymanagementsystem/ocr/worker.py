"""OCR runs in a separate process so Flask can keep serving pages."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from multiprocessing import get_context
from pathlib import Path
from queue import Empty, Full
from typing import Optional


_CTX = get_context("spawn")
_IN = None
_OUT = None
_PROC = None
_PID_FILE = Path(tempfile.gettempdir()) / "daraga_ocr_worker.pid"


def _worker_thread_count() -> str:
    """All logical cores. This is the setting that produced 13–21s death scans."""
    n = os.cpu_count() or 4
    return str(max(2, min(16, n)))


def _pid_file() -> Path:
    return _PID_FILE


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
        return
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _windows_python_processes() -> list:
    if sys.platform != "win32":
        return []
    cmd = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' or Name='pythonw.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        return []
    text = (raw or b"").decode("utf-8", "ignore").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    return list(data or [])


def kill_stale_ocr_workers(keep_pid: Optional[int] = None) -> int:
    """Stop leftover Paddle workers from a previous app.py (Windows spawn orphans)."""
    stopped = 0
    try:
        old = int(_pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        old = 0
    if old and old != keep_pid and old != os.getpid():
        _terminate_pid(old)
        stopped += 1
    try:
        _pid_file().unlink(missing_ok=True)
    except OSError:
        pass
    if sys.platform != "win32":
        return stopped
    procs = _windows_python_processes()
    live = {int(p.get("ProcessId") or 0) for p in procs}
    app_pids = {
        int(p.get("ProcessId") or 0)
        for p in procs
        if "app.py" in str(p.get("CommandLine") or "")
    }
    app_pids.add(os.getpid())
    for proc in procs:
        pid = int(proc.get("ProcessId") or 0)
        if not pid or pid in (os.getpid(), keep_pid):
            continue
        cmd = str(proc.get("CommandLine") or "")
        parent = int(proc.get("ParentProcessId") or 0)
        is_spawn = "multiprocessing.spawn" in cmd or "spawn_main" in cmd
        if is_spawn and parent not in app_pids:
            _terminate_pid(pid)
            stopped += 1
        elif is_spawn and parent not in live:
            _terminate_pid(pid)
            stopped += 1
    return stopped


def _remember_worker_pid(pid: int) -> None:
    try:
        _pid_file().write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


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
        "FLAGS_paddle_num_threads",
    )
    for key in keys:
        if force or worker:
            os.environ[key] = threads
        else:
            os.environ.setdefault(key, threads)


def set_below_normal_priority() -> None:
    """Kept for callers. OCR worker uses normal priority so reading stays quick."""
    return


def set_normal_priority() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetPriorityClass(handle, 0x00000080)
        ctypes.windll.kernel32.SetThreadPriority(
            ctypes.windll.kernel32.GetCurrentThread(),
            2,
        )
    except Exception:
        pass


def run_worker(in_q, out_q) -> None:
    # Do not inherit Flask's 2-thread cap; this process exists to run OCR quickly.
    os.environ["OCR_WORKER"] = "1"
    os.environ.setdefault("OCR_MAX_SIDE", "736")
    os.environ.setdefault("OCR_REC_BATCH", "64")
    os.environ.setdefault("FLAGS_conv_workspace_size_limit", "4096")
    os.environ["KMP_BLOCKTIME"] = "200"
    os.environ["OMP_WAIT_POLICY"] = "ACTIVE"
    os.environ["OMP_DYNAMIC"] = "FALSE"
    os.environ["MKL_DYNAMIC"] = "FALSE"
    limit_ocr_cpu_env(force=True, worker=True)
    set_normal_priority()
    try:
        import cv2

        cv2.setUseOptimized(True)
        try:
            cv2.setNumThreads(0)
        except Exception:
            pass
        from ocr.birth import extract_birth_data
        from ocr.death import extract_death_data
        from ocr.detect import detect_from_image
        from ocr.marriage import extract_marriage_data
        from ocr.shared import get_ocr
    except Exception:
        out_q.put({"type": "boot_error", "error": traceback.format_exc()})
        return

    # Load and fully warm Paddle before the first certificate so every scan
    # uses the same compiled kernels. Quality is unchanged (same 736 / PP-OCRv4).
    out_q.put({"type": "ready"})
    engine_ready = False
    engine_warmed = False
    preload_done = threading.Event()

    def _preload_engine():
        nonlocal engine_ready, engine_warmed
        try:
            get_ocr()
            engine_ready = True
            try:
                from ocr.engine import load_templates
                from ocr.field_boxes import warmup_box_specs

                for kind in ("birth", "marriage", "death"):
                    load_templates(kind)
                warmup_box_specs()
            except Exception:
                pass
            from ocr.shared import warmup_ocr

            warmup_ocr(blocking=True)
            engine_warmed = True
            print(
                f"OCR worker ready (warm) pid={os.getpid()} threads={os.environ.get('OCR_CPU_THREADS')}",
                flush=True,
            )
        except Exception as exc:
            print(f"OCR worker warmup skipped: {exc}", flush=True)
        finally:
            preload_done.set()

    threading.Thread(target=_preload_engine, daemon=True, name="ocr-preload").start()

    def _ensure_engine(job_id: str) -> None:
        nonlocal engine_ready, engine_warmed
        if engine_ready and engine_warmed:
            return
        out_q.put(
            {
                "type": "progress",
                "job_id": job_id,
                "progress": 18,
                "message": "Preparing the reader...",
            }
        )
        preload_done.wait(timeout=90)
        if not engine_ready:
            get_ocr()
            engine_ready = True
        if not engine_warmed:
            try:
                from ocr.shared import warmup_ocr

                warmup_ocr(blocking=True)
                engine_warmed = True
            except Exception:
                engine_warmed = True

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
            if job_type == "detect":
                ocr_only = bool(job.get("ocr_only"))
                if ocr_only:
                    _ensure_engine(job_id)
                    result = detect_from_image(path, ocr_fallback=True, skip_visual=True)
                else:
                    result = detect_from_image(path, ocr_fallback=False)
                    if not (result.get("doc_type") or "").strip():
                        _ensure_engine(job_id)
                        result = detect_from_image(path, ocr_fallback=True, skip_visual=True)
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
            _ensure_engine(job_id)
            if doc_type not in ("birth", "marriage", "death"):
                out_q.put(
                    {
                        "type": "progress",
                        "job_id": job_id,
                        "progress": 28,
                        "message": "Identifying document type...",
                    }
                )
                detected = detect_from_image(path, ocr_fallback=True)
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
            stop_beat = threading.Event()

            def _progress_beat() -> None:
                marks = (
                    (4, 50, "Reading the certificate..."),
                    (10, 72, "Reading the filled-in fields..."),
                    (16, 88, "Matching the form..."),
                    (19, 95, "Almost done..."),
                )
                started = time.time()
                for wait_s, pct, message in marks:
                    remain = wait_s - (time.time() - started)
                    if stop_beat.wait(timeout=max(0.05, remain)):
                        return
                    try:
                        out_q.put(
                            {
                                "type": "progress",
                                "job_id": job_id,
                                "progress": pct,
                                "message": message,
                            }
                        )
                    except Exception:
                        return

            threading.Thread(target=_progress_beat, daemon=True, name="ocr-progress").start()
            started = time.time()
            try:
                if doc_type == "birth":
                    data = extract_birth_data(path)
                elif doc_type == "marriage":
                    data = extract_marriage_data(path)
                else:
                    data = extract_death_data(path)
            finally:
                stop_beat.set()
            elapsed = time.time() - started
            print(
                f"OCR {doc_type} finished in {elapsed:.1f}s path=worker warm=1",
                flush=True,
            )
            out_q.put(
                {
                    "type": "done",
                    "job_id": job_id,
                    "doc_type": doc_type,
                    "image_filename": image_filename,
                    "data": data,
                    "elapsed_s": round(elapsed, 1),
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
    stale = kill_stale_ocr_workers()
    if stale:
        print(f"Stopped {stale} leftover OCR process(es)", flush=True)
    try:
        _IN = _CTX.Queue(maxsize=12)
        _OUT = _CTX.Queue(maxsize=32)
        _PROC = _CTX.Process(
            target=run_worker,
            args=(_IN, _OUT),
            name="daraga-ocr-worker",
            daemon=False,
        )
        _PROC.start()
        if _PROC.pid:
            _remember_worker_pid(_PROC.pid)
        return worker_is_alive()
    except Exception:
        _IN = _OUT = _PROC = None
        return False


def submit_ocr_job(job_id: str, doc_type: str, path: str, image_filename: str) -> bool:
    if not worker_is_alive() or _IN is None:
        return False
    try:
        _IN.put(
            {
                "type": "run",
                "job_id": job_id,
                "doc_type": doc_type,
                "path": path,
                "image_filename": image_filename,
            },
            timeout=8,
        )
        return True
    except Full:
        return False


def submit_detect_job(job_id: str, path: str, ocr_only: bool = False) -> bool:
    if not worker_is_alive() or _IN is None:
        return False
    try:
        _IN.put_nowait(
            {
                "type": "detect",
                "job_id": job_id,
                "path": path,
                "ocr_only": bool(ocr_only),
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
        try:
            proc.join(timeout=0.2)
        except (KeyboardInterrupt, OSError):
            pass
        if proc.is_alive():
            try:
                proc.terminate()
            except (KeyboardInterrupt, OSError):
                pass
            try:
                proc.join(timeout=0.4)
            except (KeyboardInterrupt, OSError):
                pass
        if proc.is_alive():
            try:
                proc.kill()
            except (AttributeError, KeyboardInterrupt, OSError):
                pass
    if proc is not None and proc.pid and (proc.is_alive() if hasattr(proc, "is_alive") else True):
        try:
            if proc.is_alive():
                _terminate_pid(proc.pid)
        except Exception:
            _terminate_pid(proc.pid)
    try:
        _pid_file().unlink(missing_ok=True)
    except OSError:
        pass


def get_result(timeout: float = 0.35) -> Optional[dict]:
    q = _OUT
    if q is None:
        return None
    try:
        return q.get(timeout=timeout)
    except (Empty, EOFError, OSError):
        return None
