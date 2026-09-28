import math

import pytest

from gzh_reader.ui_geometry import circular_ellipsis_centers, scrollbar_thumb_bounds


def bitmap(centers, *, ring=True, scale=2):
    width, height = 700 * scale, 55 * scale
    data = bytearray([245] * (width * height * 4))
    for x in range(width):
        for y in range(height):
            for cx, cy in centers:
                ring_pixel = ring and abs(math.hypot(x/scale-cx, y/scale-cy) - 8) <= .7
                dot = any(math.hypot(x/scale-cx-offset, y/scale-cy) <= 1.1
                          for offset in (-4, 0, 4))
                if ring_pixel or dot:
                    off = (y * width + x) * 4
                    data[off:off+3] = b'\x30\x30\x30'
    return bytes(data), width, height, width * 4, 4, 700, 55


def test_menu_glyph_is_scale_independent_and_rejects_title_ellipsis():
    for scale in (1, 2):
        found = circular_ellipsis_centers(*bitmap([(450, 24)], scale=scale))
        assert len(found) == 1 and abs(found[0][0] - 450) < 1
    assert circular_ellipsis_centers(*bitmap([(450, 24)], ring=False)) == []


def test_multiple_menus_are_reported_as_ambiguous():
    assert len(circular_ellipsis_centers(*bitmap([(250, 24), (450, 24)]))) == 2


def scrollbar_bitmap(*, scale=1, color=b'\xa0\xa0\xa0', thumb=(220, 310), padding=0):
    width, height = 320 * scale, 480 * scale
    stride = width * 4 + padding
    data = bytearray([245] * (stride * height))
    for x in range(312 * scale, 318 * scale):
        for y in range(thumb[0] * scale, thumb[1] * scale):
            offset = y * stride + x * 4
            data[offset:offset + 3] = color
    return bytes(data), width, height, stride, 4, 320, 480


def test_scrollbar_bounds_are_logical_points_at_1x_and_2x_with_padded_rows():
    for scale in (1, 2):
        assert scrollbar_thumb_bounds(*scrollbar_bitmap(scale=scale, padding=12)) == (220, 310)


def test_scrollbar_rejects_colored_content_and_ignores_toolbar_and_footer():
    assert scrollbar_thumb_bounds(*scrollbar_bitmap(color=b'\xa0\x60\xa0')) is None
    assert scrollbar_thumb_bounds(*scrollbar_bitmap(thumb=(0, 65))) is None
    assert scrollbar_thumb_bounds(*scrollbar_bitmap(thumb=(445, 480))) is None
    assert scrollbar_thumb_bounds(*scrollbar_bitmap(thumb=(220, 230), scale=2)) is None


def test_scrollbar_run_ending_at_scan_boundary_is_not_dropped():
    assert scrollbar_thumb_bounds(*scrollbar_bitmap(thumb=(400, 440))) == (400, 440)


@pytest.mark.parametrize('field,value', [(1, 0), (3, 8), (4, 2), (5, 0), (6, float('nan'))])
def test_scrollbar_rejects_invalid_bitmap_geometry(field, value):
    args = list(scrollbar_bitmap())
    args[field] = value
    with pytest.raises(ValueError):
        scrollbar_thumb_bounds(*args)


def test_scrollbar_rejects_truncated_bitmap():
    args = list(scrollbar_bitmap())
    args[0] = args[0][:-1]
    with pytest.raises(ValueError):
        scrollbar_thumb_bounds(*args)
