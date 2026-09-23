"""One-shot macOS Vision OCR helper, isolated from the collection process."""

from __future__ import annotations

import json
import sys


def recognize(window_number: int, width: float, height: float) -> list[dict]:
    import Quartz
    import Vision

    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_number,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise RuntimeError("微信窗口截图不可用")
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"OCR 失败：{error}")
    pixel_w = float(Quartz.CGImageGetWidth(image))
    pixel_h = float(Quartz.CGImageGetHeight(image))
    sx = width / pixel_w
    sy = height / pixel_h
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


def main() -> int:
    try:
        number, width, height = sys.argv[1:4]
        print(json.dumps({"lines": recognize(int(number), float(width), float(height))},
                         ensure_ascii=False))
        return 0
    except Exception as exc:  # child stderr is bounded and sanitized by parent
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
