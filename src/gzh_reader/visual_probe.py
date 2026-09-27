"""Check whether this process can capture and read a known local window.

The probe never inspects a chat or stores a screenshot. It creates a short-lived
window containing a fixed marker, then asks the existing OCR worker to read it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time


MARKER = "GZH VISUAL CHECK 8642"
WINDOW_TITLE = "gzh-reader visual check"


def probe_capture() -> dict:
    import AppKit
    import Quartz

    app = AppKit.NSApplication.sharedApplication()
    frame = AppKit.NSMakeRect(140, 140, 620, 240)
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, AppKit.NSWindowStyleMaskTitled, AppKit.NSBackingStoreBuffered, False,
    )
    window.setTitle_(WINDOW_TITLE)
    label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(25, 75, 570, 85))
    label.setStringValue_(MARKER)
    label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(27))
    label.setEditable_(False)
    label.setSelectable_(False)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    window.contentView().addSubview_(label)
    window.makeKeyAndOrderFront_(None)
    window.displayIfNeeded()
    app.activateIgnoringOtherApps_(True)
    try:
        deadline = time.monotonic() + 3
        info = None
        while time.monotonic() < deadline:
            AppKit.NSRunLoop.currentRunLoop().runUntilDate_(
                AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )
            rows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
            )
            info = next(
                (row for row in rows if row.get(Quartz.kCGWindowName) == WINDOW_TITLE),
                None,
            )
            if info is not None:
                break
        if info is None:
            return {"window_visible": False, "capture_readable": False,
                    "reason": "测试窗口未出现在当前桌面"}
        bounds = info.get(Quartz.kCGWindowBounds, {})
        result = subprocess.run(
            [sys.executable, "-m", "gzh_reader.ocr_worker",
             str(info[Quartz.kCGWindowNumber]),
             str(bounds.get("Width", 620)), str(bounds.get("Height", 240))],
            capture_output=True, text=True, check=False, timeout=15,
        )
        if result.returncode != 0:
            return {"window_visible": True, "capture_readable": False,
                    "reason": "测试窗口截图或文字识别失败"}
        lines = json.loads(result.stdout).get("lines", [])
        observed = " ".join(str(line.get("text", "")) for line in lines)
        readable = "GZH" in observed and "8642" in observed
        return {"window_visible": True, "capture_readable": readable,
                "ocr_line_count": len(lines),
                "reason": "ok" if readable else "测试窗口可见，但固定文字未被识别"}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return {"window_visible": True, "capture_readable": False,
                "reason": "测试窗口截图或文字识别超时/返回异常"}
    finally:
        window.orderOut_(None)
        window.close()


def probe_wechat(controller=None) -> dict:
    """Return only counts and state, never OCR text from a private window."""
    if controller is None:
        from .human_agent import MacHumanController

        controller = MacHumanController()
    try:
        controller.activate()
        windows = controller.windows()
        candidates = [
            item for item in windows
            if item.onscreen and item.width >= 300
            and item.title in {"微信", "微信 (窗口)", "公众号"}
        ]
        if not candidates:
            offscreen_article = any(
                item.title == "微信 (窗口)" and item.width >= 300
                and not item.onscreen for item in windows
            )
            return {"window_visible": False, "capture_readable": False,
                    "reason": (
                        "微信文章窗口存在但不在当前可见桌面；请手动在微信打开目标文章"
                        if offscreen_article else "微信内容窗口当前不可见"
                    )}
        window = max(candidates, key=lambda item: item.width * item.height)
        line_count = len(controller.ocr(window))
        display_lines = None
        if not line_count and controller.__class__.__name__ == "MacHumanController":
            try:
                display = subprocess.run(
                    [sys.executable, "-m", "gzh_reader.ocr_worker", "--display-count",
                     str(window.x), str(window.y), str(window.width), str(window.height)],
                    capture_output=True, text=True, check=False, timeout=12,
                )
                if display.returncode == 0:
                    display_lines = json.loads(display.stdout)
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
                pass
        return {"window_visible": True, "capture_readable": line_count > 0,
                "ocr_line_count": line_count,
                "sharing_state": window.sharing_state,
                "display_ocr": display_lines,
                "reason": ("ok" if line_count else
                           "微信窗口未向系统共享画面，截图无法读取"
                           if window.sharing_state == 0 else
                           "微信窗口可见，但当前截图没有可识别文字")}
    except RuntimeError:
        return {"window_visible": False, "capture_readable": False,
                "reason": "微信窗口检查失败；请查看前台微信是否正常显示"}


def probe_all() -> dict:
    calibration = probe_capture()
    wechat = probe_wechat() if calibration["capture_readable"] else None
    return {"calibration": calibration, "wechat": wechat}


def main() -> int:
    payload = probe_all()
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["calibration"]["capture_readable"] and (
        payload["wechat"] or {}
    ).get("capture_readable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
