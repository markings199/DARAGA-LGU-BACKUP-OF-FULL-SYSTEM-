"""Straighten a crooked scan. Leave an already-square certificate untouched."""
from __future__ import annotations

import math
import os
from pathlib import Path

import cv2
import numpy as np

MIN_SKEW = 0.4


def _imread(path: Path):
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _imwrite(path: Path, img) -> bool:
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), 98]
        ext = ".jpg"
    else:
        params = [int(cv2.IMWRITE_PNG_COMPRESSION), 2]
        ext = ".png"
    ok, buf = cv2.imencode(ext, img, params)
    if not ok or buf is None:
        return False
    buf.tofile(str(path))
    return path.is_file() and path.stat().st_size > 0


def _replace_file(tmp: Path, src: Path) -> bool:
    try:
        os.replace(tmp, src)
        return src.is_file() and src.stat().st_size > 0
    except OSError:
        try:
            src.write_bytes(tmp.read_bytes())
            tmp.unlink(missing_ok=True)
            return src.is_file() and src.stat().st_size > 0
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False


def _exif_upright(path: Path, img):
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as pil:
            info = pil.getexif()
            orientation = int(info.get(274) or 1) if info else 1
            if orientation in (0, 1):
                return img, False
            oriented = ImageOps.exif_transpose(pil)
            rgb = np.array(oriented.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), True
    except Exception:
        return img, False


def _normalize_angle(degrees: float) -> float:
    ang = float(degrees)
    while ang > 45:
        ang -= 90
    while ang <= -45:
        ang += 90
    return ang


def _median_angle(angles: list[float]) -> float:
    if not angles:
        return 0.0
    angles = sorted(angles)
    return float(angles[len(angles) // 2])


def _working_gray(gray, max_side: int = 1100):
    height, width = gray.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return gray, 1.0
    scale = max_side / float(longest)
    small = cv2.resize(
        gray,
        (max(32, int(round(width * scale))), max(32, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return small, scale


def _hough_skew_angle(gray) -> float:
    height, width = gray.shape
    longest = max(height, width)
    scale = 1100.0 / longest if longest > 1100 else 1.0
    small = (
        cv2.resize(gray, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        if scale < 1
        else gray
    )
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(blur)
    best = 0.0
    best_n = 0
    for c1, c2, thr, min_frac in (
        (50, 150, 50, 0.08),
        (40, 120, 40, 0.06),
        (30, 90, 30, 0.05),
    ):
        edges = cv2.Canny(clahe, c1, c2)
        min_len = max(28, int(min(small.shape) * min_frac))
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180.0,
            threshold=thr,
            minLineLength=min_len,
            maxLineGap=22,
        )
        if lines is None or len(lines) < 8:
            continue
        horiz: list[float] = []
        for x1, y1, x2, y2 in lines[:, 0]:
            dx = float(x2 - x1)
            dy = float(y2 - y1)
            if abs(dx) < 2 and abs(dy) < 2:
                continue
            ang = _normalize_angle(math.degrees(math.atan2(dy, dx)))
            if abs(ang) <= 18:
                horiz.append(ang)
        if len(horiz) < 8:
            continue
        mid = _median_angle(horiz)
        if abs(mid) >= MIN_SKEW and len(horiz) > best_n:
            best = float(mid)
            best_n = len(horiz)
            if best_n >= 20:
                break
    return best if abs(best) >= MIN_SKEW else 0.0


def _page_mask(gray):
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    return mask


def _largest_page(gray):
    height, width = gray.shape
    mask = _page_mask(gray)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    page = max(contours, key=cv2.contourArea)
    if cv2.contourArea(page) < 0.22 * float(width * height):
        return None
    return page


def _contour_skew_angle(gray) -> float:
    page = _largest_page(gray)
    if page is None:
        return 0.0
    rect = cv2.minAreaRect(page)
    ang = float(rect[2])
    if rect[1][0] < rect[1][1]:
        ang = ang - 90.0
    ang = _normalize_angle(ang)
    if abs(ang) < MIN_SKEW or abs(ang) > 18:
        return 0.0
    return ang


def _order_quad(pts):
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(4)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _page_quad(gray):
    page = _largest_page(gray)
    if page is None:
        return None
    peri = cv2.arcLength(page, True)
    approx = cv2.approxPolyDP(page, 0.02 * peri, True)
    if len(approx) == 4:
        return _order_quad(approx)
    box = cv2.boxPoints(cv2.minAreaRect(page))
    return _order_quad(box)


def _quad_usable(quad, width: int, height: int) -> bool:
    if quad is None:
        return False
    if np.any(quad[:, 0] < -6) or np.any(quad[:, 1] < -6):
        return False
    if np.any(quad[:, 0] > width + 6) or np.any(quad[:, 1] > height + 6):
        return False
    area = abs(cv2.contourArea(quad.reshape(-1, 1, 2)))
    if area < 0.40 * float(width * height) or area > 0.97 * float(width * height):
        return False
    tl, tr, br, bl = quad
    top = float(np.linalg.norm(tr - tl))
    bottom = float(np.linalg.norm(br - bl))
    left = float(np.linalg.norm(bl - tl))
    right = float(np.linalg.norm(br - tr))
    if min(top, bottom, left, right) < 80:
        return False
    width_ratio = min(top, bottom) / max(top, bottom)
    height_ratio = min(left, right) / max(left, right)
    return width_ratio >= 0.82 and height_ratio >= 0.82


def _quad_is_crooked(quad, width: int, height: int) -> bool:
    if not _quad_usable(quad, width, height):
        return False
    corners = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    drift = float(np.mean(np.linalg.norm(quad - corners, axis=1)))
    return drift > 0.03 * float(max(width, height))


def _warp_quad(img, quad):
    tl, tr, br, bl = quad
    width_a = float(np.linalg.norm(br - bl))
    width_b = float(np.linalg.norm(tr - tl))
    height_a = float(np.linalg.norm(tr - br))
    height_b = float(np.linalg.norm(tl - bl))
    max_w = int(round(max(width_a, width_b)))
    max_h = int(round(max(height_a, height_b)))
    if max_w < 200 or max_h < 200:
        return None
    dest = np.array(
        [[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(quad, dest)
    return cv2.warpPerspective(
        img,
        matrix,
        (max_w, max_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def _rotate_keep_quality(img, angle: float):
    height, width = img.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos_a = abs(matrix[0, 0])
    sin_a = abs(matrix[0, 1])
    new_w = int(height * sin_a + width * cos_a)
    new_h = int(height * cos_a + width * sin_a)
    matrix[0, 2] += (new_w / 2.0) - center[0]
    matrix[1, 2] += (new_h / 2.0) - center[1]
    return cv2.warpAffine(
        img,
        matrix,
        (new_w, new_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def _empty_bed(line) -> bool:
    if line.size == 0:
        return False
    std = float(line.std())
    bright = float((line > 242).mean())
    return bright >= 0.92 and std <= 14


def _dark_lid(line, interior) -> bool:
    """Scanner lid is a dark slab. A printed box is a thin dark rule then paper."""
    if line.size == 0:
        return False
    mean = float(line.mean())
    dark = float((line < 45).mean())
    if not (mean < 85 and dark >= 0.28):
        return False
    if interior.size == 0:
        return True
    return float(interior.mean()) < 130


def _junk_depth_cols(gray, from_right: bool) -> int:
    height, width = gray.shape
    max_depth = max(8, int(width * 0.14))
    last_junk = -1
    good_run = 0
    started = False
    for i in range(max_depth):
        x = width - 1 - i if from_right else i
        if from_right:
            interior = gray[:, max(0, x - 10) : x]
        else:
            interior = gray[:, x + 1 : min(width, x + 11)]
        junk = _empty_bed(gray[:, x]) or _dark_lid(gray[:, x], interior)
        if junk:
            started = True
            last_junk = i
            good_run = 0
            continue
        if not started:
            good_run += 1
            if good_run >= 4:
                return 0
            continue
        good_run += 1
        if good_run >= 4:
            break
    depth = last_junk + 1
    return depth if depth >= 3 else 0


def _junk_depth_rows(gray, from_bottom: bool) -> int:
    height, width = gray.shape
    max_depth = max(8, int(height * 0.14))
    last_junk = -1
    good_run = 0
    started = False
    for i in range(max_depth):
        y = height - 1 - i if from_bottom else i
        if from_bottom:
            interior = gray[max(0, y - 10) : y, :]
        else:
            interior = gray[y + 1 : min(height, y + 11), :]
        junk = _empty_bed(gray[y, :]) or _dark_lid(gray[y, :], interior)
        if junk:
            started = True
            last_junk = i
            good_run = 0
            continue
        if not started:
            good_run += 1
            if good_run >= 4:
                return 0
            continue
        good_run += 1
        if good_run >= 4:
            break
    depth = last_junk + 1
    return depth if depth >= 3 else 0


def _trim_scanner_margins(img):
    """Remove scanner lid / empty bed only. Do not crop a square certificate."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    x0 = _junk_depth_cols(gray, False)
    x_right = _junk_depth_cols(gray, True)
    y0 = _junk_depth_rows(gray, False)
    y_bottom = _junk_depth_rows(gray, True)
    x1 = width - x_right
    y1 = height - y_bottom
    if y0 > int(height * 0.04):
        y0 = 0
    if x0 <= 2 and y0 <= 2 and x1 >= width - 2 and y1 >= height - 2:
        return img, False
    if (x1 - x0) < 0.72 * width or (y1 - y0) < 0.72 * height:
        return img, False
    return img[y0:y1, x0:x1], True


def _measure_skew(gray) -> float:
    angle = _hough_skew_angle(gray)
    if abs(angle) < MIN_SKEW:
        angle = _contour_skew_angle(gray)
    return angle if abs(angle) >= MIN_SKEW else 0.0


def straighten_document_file(path: str | os.PathLike) -> bool:
    """
    Level a crooked scan/upload. If the certificate is already square, do nothing.
    """
    src = Path(path)
    if not src.is_file():
        return False
    img = _imread(src)
    if img is None or img.size == 0:
        return False
    img, changed = _exif_upright(src, img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    work, _work_scale = _working_gray(gray)
    angle = _measure_skew(work)
    if abs(angle) >= MIN_SKEW:
        img = _rotate_keep_quality(img, angle)
        changed = True
    # Keep the full page. Do not warp or trim edges — certificates must stay complete.
    if not changed:
        return False
    tmp = src.with_name(src.stem + ".straight.tmp" + src.suffix)
    if not _imwrite(tmp, img):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return _replace_file(tmp, src)
