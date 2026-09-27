"""Small visual checks for controls not exposed by WeChat Accessibility.

Coordinates are window-local points. No title spacing or guessed tab widths.
"""

from __future__ import annotations

import math


def scrollbar_thumb_bounds(
    data: bytes, pixel_width: int, pixel_height: int, row_bytes: int,
    bytes_per_pixel: int, logical_width: float, logical_height: float,
) -> tuple[float, float] | None:
    """Locate a gray scrollbar thumb and return its window-local y bounds.

    Only the rightmost 16 logical points below the toolbar are inspected.
    All geometry, including the minimum thumb length, is in logical points;
    a Retina bitmap must produce the same answer as its 1x counterpart.
    RGB/BGR and alpha-last RGBA/BGRA buffers are supported. Reject invalid
    buffer metadata before indexing so an incomplete image cannot yield a
    plausible control coordinate.
    """
    if (
        not all(isinstance(value, int) and value > 0 for value in
                (pixel_width, pixel_height, row_bytes, bytes_per_pixel))
        or bytes_per_pixel not in (3, 4)
        or not all(math.isfinite(value) and value > 0 for value in
                   (logical_width, logical_height))
        or row_bytes < pixel_width * bytes_per_pixel
        or len(data) < (pixel_height - 1) * row_bytes + pixel_width * bytes_per_pixel
    ):
        raise ValueError("Invalid scrollbar bitmap geometry or data")
    sx, sy = pixel_width / logical_width, pixel_height / logical_height
    left = max(0, math.ceil((logical_width - 16) * sx))
    right = min(pixel_width, math.floor((logical_width - 2) * sx))
    top = min(pixel_height, math.ceil(70 * sy))
    bottom = max(0, min(pixel_height, math.floor((logical_height - 40) * sy)))
    if left >= right or top >= bottom:
        return None
    minimum_length = math.ceil(20 * sy)
    runs: list[tuple[int, int]] = []
    for x in range(left, right):
        start: int | None = None
        for y in range(top, bottom):
            offset = y * row_bytes + x * bytes_per_pixel
            color = data[offset:offset + 3]
            gray = max(color) - min(color) < 12 and 110 <= color[0] <= 220
            if gray and start is None:
                start = y
            elif not gray and start is not None:
                if y - start >= minimum_length:
                    runs.append((start, y))
                start = None
        if start is not None and bottom - start >= minimum_length:
            runs.append((start, bottom))
    if not runs:
        return None
    start, end = max(runs, key=lambda bounds: bounds[1] - bounds[0])
    return start / sy, end / sy


def circular_ellipsis_centers(
    data: bytes, pixel_width: int, pixel_height: int, row_bytes: int,
    bytes_per_pixel: int, logical_width: float, logical_height: float,
) -> list[tuple[float, float]]:
    """Find three separate dark dots enclosed by a dark circle in the tab bar.

    Input is an 8-bit RGB/BGR bitmap (alpha, if present, is last). Requiring
    both the three dots and ring rejects title ellipses and reload icons.
    """
    if bytes_per_pixel < 3 or min(pixel_width, pixel_height) <= 0:
        return []
    sx, sy = pixel_width / logical_width, pixel_height / logical_height
    left, right = int(80 * sx), min(pixel_width, int((logical_width - 80) * sx))
    top, bottom = int(10 * sy), min(pixel_height, int(40 * sy))

    def dark(x: int, y: int, threshold: int = 150) -> bool:
        if not (0 <= x < pixel_width and 0 <= y < pixel_height):
            return False
        offset = y * row_bytes + x * bytes_per_pixel
        color = data[offset:offset + 3]
        return len(color) == 3 and max(color) < threshold and max(color) - min(color) < 35

    remaining = {
        (x, y) for y in range(top, bottom) for x in range(left, right)
        if dark(x, y)
    }
    dots: list[tuple[float, float]] = []
    while remaining:
        seed = remaining.pop()
        component, queue = [seed], [seed]
        while queue:
            x, y = queue.pop()
            for neighbor in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.append(neighbor)
                    queue.append(neighbor)
        xs, ys = zip(*component)
        width = (max(xs) - min(xs) + 1) / sx
        height = (max(ys) - min(ys) + 1) / sy
        if 0.7 <= width <= 3.8 and 0.7 <= height <= 3.8 and len(component) >= sx * sy:
            dots.append(((min(xs) + max(xs)) / 2 / sx, (min(ys) + max(ys)) / 2 / sy))
    dots.sort()
    results = []
    for a in dots:
        for b in dots:
            gap = b[0] - a[0]
            if not 2 <= gap <= 6 or abs(a[1] - b[1]) > 1:
                continue
            for c in dots:
                if abs(c[0] - b[0] - gap) > 1 or abs(c[1] - b[1]) > 1:
                    continue
                cx, cy = b
                if not 16 <= cy <= 33:
                    continue
                ring = 0
                for step in range(24):
                    angle = step * math.tau / 24
                    if any(dark(round((cx + radius * math.cos(angle)) * sx),
                                round((cy + radius * math.sin(angle)) * sy), 180)
                           for radius in (6.5, 7, 7.5, 8, 8.5, 9)):
                        ring += 1
                if ring >= 21 and not any(abs(cx-x) < 4 for x, _ in results):
                    results.append((cx, cy))
    return sorted(results)
