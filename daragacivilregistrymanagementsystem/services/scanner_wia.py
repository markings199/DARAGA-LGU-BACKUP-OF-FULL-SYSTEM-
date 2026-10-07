"""
Windows WIA (Windows Image Acquisition) scanner support.
Works with most USB scanners and printer/scanner combos that expose a WIA driver.

Different vendors use different default formats and item indices (flatbed vs ADF);
scan_to_file() tries multiple items and formats for broader compatibility.
Requires pywin32 on Windows. Safe no-op on other platforms.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

# Optional: convert BMP to PNG for smaller size and web use
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

WIA_AVAILABLE = False
if sys.platform == "win32":
    try:
        import win32com.client
        import pythoncom
        WIA_AVAILABLE = True
    except ImportError:
        pass

# WIA device type (see WiaDeviceType)
WIA_DEVICE_TYPE_SCANNER = 1
WIA_DEVICE_TYPE_CAMERA = 2

# Common WIA format GUIDs (Transfer / SaveFile)
WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"


def _device_info_name(di) -> str:
    name = None
    try:
        for j in range(1, di.Properties.Count + 1):
            p = di.Properties(j)
            if getattr(p, "Name", None) == "Name" or str(p.Name) == "Name":
                name = str(p.Value)
                break
    except Exception:
        pass
    if not name:
        name = getattr(di, "Name", None) or "Unknown device"
    return str(name)


def _device_info_type(di):
    """Return WIA device type int, or None if unknown."""
    try:
        return int(di.Type)
    except Exception:
        pass
    try:
        return int(di.Properties("Type").Value)
    except Exception:
        return None


def _format_guids():
    """Preferred order: BMP (widest driver support), then JPEG, then PNG."""
    guids = []
    for const_name, fallback, ext in (
        ("wiaFormatBMP", WIA_FORMAT_BMP, ".bmp"),
        ("wiaFormatJPEG", WIA_FORMAT_JPEG, ".jpg"),
        ("wiaFormatPNG", WIA_FORMAT_PNG, ".png"),
    ):
        try:
            g = getattr(win32com.client.constants, const_name)
            guids.append((g, ext))
        except Exception:
            guids.append((fallback, ext))
    # De-dupe while keeping order
    seen = set()
    out = []
    for g, ext in guids:
        key = str(g).upper()
        if key not in seen:
            seen.add(key)
            out.append((g, ext))
    return out


def list_scanners():
    """
    Return scanners detected by WIA.
    Each item: {"id": 1-based index, "name": str, "device_type": int|None}.

    Devices that report as Scanner (type 1) are listed first; if none report a type,
    all WIA devices are returned so odd drivers can still be selected.
    """
    if not WIA_AVAILABLE:
        return []
    try:
        pythoncom.CoInitialize()
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        result = []
        for i in range(1, dm.DeviceInfos.Count + 1):
            di = dm.DeviceInfos(i)
            dtype = _device_info_type(di)
            name = _device_info_name(di)
            result.append({"id": i, "name": name, "device_type": dtype})

        scanners = [r for r in result if r["device_type"] == WIA_DEVICE_TYPE_SCANNER]
        if scanners:
            return scanners
        # No explicit scanner type — include everything except obvious cameras
        non_camera = [r for r in result if r["device_type"] != WIA_DEVICE_TYPE_CAMERA]
        return non_camera if non_camera else result
    except Exception:
        return []
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _convert_to_png(src_path: Path, out_png: Path, save_as_png: bool) -> Path:
    """Return path to final image (PNG if save_as_png and conversion ok, else source)."""
    if not save_as_png:
        return src_path
    if src_path.suffix.lower() == ".png" and src_path.exists():
        return src_path
    if not src_path.exists():
        return src_path
    if HAS_PIL:
        try:
            with Image.open(src_path) as im:
                if im.mode not in ("RGB", "RGBA", "L"):
                    im = im.convert("RGB")
                im.save(out_png, "PNG")
            if out_png.exists():
                if src_path.resolve() != out_png.resolve():
                    try:
                        src_path.unlink()
                    except OSError:
                        pass
                return out_png
        except Exception:
            pass
    if HAS_CV2 and src_path.suffix.lower() in (".bmp", ".jpg", ".jpeg", ".png"):
        try:
            img = cv2.imread(str(src_path))
            if img is not None:
                cv2.imwrite(str(out_png), img)
                if out_png.exists():
                    try:
                        src_path.unlink()
                    except OSError:
                        pass
                    return out_png
        except Exception:
            pass
    return src_path


def scan_to_file(device_index=1, output_dir=None, save_as_png=True):
    """
    Scan from the given WIA device (1-based DeviceInfos index) and save to a file.
    Tries each scanner item (flatbed / ADF slots) and BMP/JPEG/PNG transfer formats.

    Returns (success: bool, path_or_error: str).
    """
    if not WIA_AVAILABLE:
        return False, "Scanner not supported on this system (Windows + pywin32 required)."
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir())
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, (
            "Cannot save the scan on the office server. "
            "The scan was not stored. Check that server storage is available and has free space."
        )

    stamp = int(time.time() * 1000)
    out_png = output_dir / f"scan_{stamp}.png"

    try:
        pythoncom.CoInitialize()
        dm = win32com.client.Dispatch("WIA.DeviceManager")
        if device_index < 1 or device_index > dm.DeviceInfos.Count:
            return False, f"No device at index {device_index}. Click Detect scanner and pick a listed device."

        device_info = dm.DeviceInfos(device_index)
        device = device_info.Connect()

        try:
            n_items = int(device.Items.Count)
        except Exception:
            n_items = 1
        n_items = max(1, min(n_items, 8))

        format_list = _format_guids()
        last_error = "No image received from scanner."

        for item_idx in range(1, n_items + 1):
            for fmt_guid, ext in format_list:
                out_src = output_dir / f"scan_{stamp}_i{item_idx}{ext}"
                try:
                    item = device.Items(item_idx)
                    wia_image = item.Transfer(fmt_guid)
                    wia_image.SaveFile(str(out_src))
                except Exception as e:
                    last_error = str(e)
                    continue
                if out_src.exists() and out_src.stat().st_size > 0:
                    final = _convert_to_png(out_src, out_png, save_as_png)
                    return True, str(final)

        return False, (
            f"Scan failed for this device ({last_error}). "
            "Try another device in the list, place the document on the glass, "
            "or use the manufacturer scan app once to verify the driver, then Fallback upload."
        )
    except Exception as e:
        return False, str(e)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
