"""One-shot macOS Vision OCR helper, isolated from the collection process."""

from __future__ import annotations

import json
import os
import sys


def _trace(stage: str) -> None:
    if os.environ.get("GZH_OCR_TRACE") == "1":
        print(stage, file=sys.stderr, flush=True)


def recognize(window_number: int, width: float, height: float,
              top_fraction: float = 1.0,
              visible_origin: tuple[float, float] | None = None) -> list[dict]:
    import Quartz
    import Vision

    _trace("capture_start")
    if visible_origin is None:
        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
            window_number, Quartz.kCGWindowImageBoundsIgnoreFraming,
        )
    else:
        # Menus are compositor overlays absent from a base-window image.
        # CGWindowListCreateImage(...OnScreenOnly) has intermittently stalled
        # inside capture_start on this Mac, while a main-display snapshot is
        # fast. Crop that snapshot to the already-validated foreground window.
        display_id = Quartz.CGMainDisplayID()
        bounds = Quartz.CGDisplayBounds(display_id)
        origin_x, origin_y = visible_origin
        if (origin_x < bounds.origin.x or origin_y < bounds.origin.y
                or origin_x + width > bounds.origin.x + bounds.size.width
                or origin_y + height > bounds.origin.y + bounds.size.height):
            raise RuntimeError("微信菜单区域不在主显示器内，拒绝跨屏截图")
        display_image = Quartz.CGDisplayCreateImage(display_id)
        if display_image is None:
            _trace("display_capture_retry")
            display_image = Quartz.CGDisplayCreateImage(display_id)
        if display_image is None:
            raise RuntimeError("主显示器截图不可用")
        scale_x = Quartz.CGImageGetWidth(display_image) / bounds.size.width
        scale_y = Quartz.CGImageGetHeight(display_image) / bounds.size.height
        image = Quartz.CGImageCreateWithImageInRect(
            display_image,
            Quartz.CGRectMake(
                (origin_x - bounds.origin.x) * scale_x,
                (origin_y - bounds.origin.y) * scale_y,
                width * scale_x, height * scale_y,
            ),
        )
    if image is None:
        raise RuntimeError("微信窗口截图不可用")
    _trace("capture_done")
    if not 0 < top_fraction <= 1:
        raise ValueError("OCR 裁剪比例无效")
    full_pixel_w = float(Quartz.CGImageGetWidth(image))
    full_pixel_h = float(Quartz.CGImageGetHeight(image))
    if top_fraction < 1:
        image = Quartz.CGImageCreateWithImageInRect(
            image, Quartz.CGRectMake(0, 0, full_pixel_w,
                                     max(1, int(full_pixel_h * top_fraction))),
        )
        if image is None:
            raise RuntimeError("微信窗口顶部截图不可用")
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    _trace("recognize_start")
    ok, error = handler.performRequests_error_([request], None)
    _trace("recognize_done")
    if not ok:
        raise RuntimeError(f"OCR 失败：{error}")
    pixel_w = float(Quartz.CGImageGetWidth(image))
    pixel_h = float(Quartz.CGImageGetHeight(image))
    sx = width / full_pixel_w
    sy = height / full_pixel_h
    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        box = observation.boundingBox()
        lines.append({
            "text": str(candidates[0].string()),
            "x": float(box.origin.x * pixel_w * sx),
            "y": float((1 - box.origin.y - box.size.height) * pixel_h * sy),
            "width": float(box.size.width * pixel_w * sx),
            "height": float(box.size.height * pixel_h * sy),
        })
    return lines


def display_line_count(rect: tuple[float, float, float, float] | None = None) -> dict:
    """Probe the active display without returning or saving private text."""
    import Quartz
    import Vision

    image = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
    if image is None:
        return {"line_count": 0, "inside_window_count": None}
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    ok, _ = handler.performRequests_error_([request], None)
    observations = request.results() or [] if ok else []
    inside = None
    if rect is not None:
        display = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        scale_x = Quartz.CGImageGetWidth(image) / display.size.width
        scale_y = Quartz.CGImageGetHeight(image) / display.size.height
        left = (rect[0] - display.origin.x) * scale_x
        top = (rect[1] - display.origin.y) * scale_y
        right = left + rect[2] * scale_x
        bottom = top + rect[3] * scale_y
        width = Quartz.CGImageGetWidth(image)
        height = Quartz.CGImageGetHeight(image)
        inside = 0
        for observation in observations:
            box = observation.boundingBox()
            x = (box.origin.x + box.size.width / 2) * width
            y = (1 - box.origin.y - box.size.height / 2) * height
            inside += left <= x <= right and top <= y <= bottom
    return {"line_count": len(observations), "inside_window_count": inside}


def menu_centers(window_number: int, width: float, height: float) -> list[tuple[float, float]]:
    import Quartz

    from .ui_geometry import circular_ellipsis_centers

    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
        window_number, Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise RuntimeError("文章标签栏截图不可用")
    return circular_ellipsis_centers(
        bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))),
        Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image),
        Quartz.CGImageGetBytesPerRow(image), Quartz.CGImageGetBitsPerPixel(image) // 8,
        width, height,
    )


def scrollbar_thumb(window_number: int, width: float, height: float) -> tuple[float, float] | None:
    """Keep every scrollbar capture and its CGImage inside this disposable worker."""
    import Quartz

    from .ui_geometry import scrollbar_thumb_bounds

    _trace("capture_start")
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
        window_number, Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise RuntimeError("文章滚动条截图不可用")
    _trace("capture_done")
    return scrollbar_thumb_bounds(
        bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))),
        Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image),
        Quartz.CGImageGetBytesPerRow(image), Quartz.CGImageGetBitsPerPixel(image) // 8,
        width, height,
    )


def main() -> int:
    try:
        if sys.argv[1:2] == ["--menu-center"]:
            number, width, height = sys.argv[2:5]
            print(json.dumps({"centers": menu_centers(int(number), float(width), float(height))}))
            return 0
        if sys.argv[1:2] == ["--scrollbar-thumb"]:
            number, width, height = sys.argv[2:5]
            print(json.dumps({"thumb": scrollbar_thumb(int(number), float(width), float(height))}))
            return 0
        if sys.argv[1:2] == ["--display-count"]:
            rect = tuple(map(float, sys.argv[2:6])) if len(sys.argv) == 6 else None
            print(json.dumps(display_line_count(rect)))
            return 0
        number, width, height = sys.argv[1:4]
        top_fraction = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
        visible_origin = (
            (float(sys.argv[6]), float(sys.argv[7]))
            if sys.argv[5:6] == ["--visible"] else None
        )
        print(json.dumps({"lines": recognize(int(number), float(width), float(height),
                                             top_fraction, visible_origin)},
                         ensure_ascii=False))
        return 0
    except Exception as exc:  # child stderr is bounded and sanitized by parent
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    code = main()
    # Vision disposes bridged Python objects on native dispatch queues after
    # recognition returns. CPython finalization can race those callbacks:
    # observed macOS crash: OC_PythonArray dealloc -> PyGILState_Ensure ->
    # pthread_exit, FOUNDATION/SIGKILL. This process owns no persistent state.
    # Deliver the complete result, then let the OS reclaim it atomically.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
