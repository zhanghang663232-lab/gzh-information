from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .models import Account


class AgentState(StrEnum):
    OBSERVE = "observe"
    OPEN = "open"
    VERIFY = "verify"
    CLOSE = "close"
    SCROLL = "scroll"
    COMPLETE = "complete"
    INCOMPLETE = "list_incomplete"


@dataclass(slots=True)
class Viewport:
    fingerprint: str
    article_urls: list[str]
    at_verified_bottom: bool = False


class MacObserver(Protocol):
    def observe(self) -> Viewport: ...
    def open_next(self) -> None: ...
    def close_detail(self) -> None: ...
    def scroll(self) -> None: ...


class MacWechatAgentProvider:
    """Experimental Accessibility/Vision fallback state machine.

    OCR labels are navigation hints only. Only URLs observed from real WeChat requests
    may become ArticleSeed records. Without a verified bottom signal the result is
    deliberately marked incomplete.
    """

    def __init__(self, observer: MacObserver, max_repeat: int = 3, max_steps: int = 5000):
        self.observer = observer
        self.max_repeat = max_repeat
        self.max_steps = max_steps
        self.state = AgentState.OBSERVE

    def enumerate_articles(self, account: Account) -> tuple[list[str], AgentState]:
        urls: list[str] = []
        repeats = 0
        previous = ""
        for _ in range(self.max_steps):
            view = self.observer.observe()
            if view.fingerprint == previous:
                repeats += 1
            else:
                repeats = 0
            previous = view.fingerprint
            urls.extend(url for url in view.article_urls if url not in urls)
            if view.at_verified_bottom and repeats >= 1:
                self.state = AgentState.COMPLETE
                return urls, self.state
            if repeats >= self.max_repeat:
                self.state = AgentState.INCOMPLETE
                return urls, self.state
            self.state = AgentState.SCROLL
            self.observer.scroll()
        self.state = AgentState.INCOMPLETE
        return urls, self.state


class NativeMacObserver:
    """Real macOS screenshot/OCR/scroll adapter used by the experimental fallback.

    Article URLs are read only from the sanitized MITM URL log; Vision OCR is used
    solely to detect a visible terminal marker and never becomes archive data.
    """

    BOTTOM_MARKERS = ("没有更多", "已无更多", "到底了", "暂无更多")

    def __init__(self, article_url_log: Path):
        self.article_url_log = article_url_log

    @staticmethod
    def accessibility_trusted(prompt: bool = False) -> bool:
        try:
            import Quartz
            options = {Quartz.kAXTrustedCheckOptionPrompt: prompt}
            return bool(Quartz.AXIsProcessTrustedWithOptions(options))
        except ImportError:
            return False

    def observe(self) -> Viewport:  # pragma: no cover - requires an interactive Mac desktop
        if not self.accessibility_trusted(prompt=True):
            raise PermissionError("请在系统设置 → 隐私与安全性 → 辅助功能中允许本应用")
        import AppKit
        import Quartz
        import Vision

        workspace = AppKit.NSWorkspace.sharedWorkspace()
        wechat = next(
            (app for app in workspace.runningApplications()
             if str(app.localizedName() or "").lower() in {"wechat", "微信"}),
            None,
        )
        if wechat is None:
            raise RuntimeError("没有检测到正在运行的 Mac 微信")
        wechat.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        time.sleep(0.4)

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        )
        candidates = [
            item for item in windows
            if str(item.get(Quartz.kCGWindowOwnerName, "")).lower() in {"wechat", "微信"}
            and int(item.get(Quartz.kCGWindowLayer, 1)) == 0
        ]
        if not candidates:
            raise RuntimeError("没有找到可见的微信窗口")
        target = max(candidates, key=lambda item: item[Quartz.kCGWindowBounds]["Width"] * item[Quartz.kCGWindowBounds]["Height"])
        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            int(target[Quartz.kCGWindowNumber]),
            Quartz.kCGWindowImageDefault,
        )
        bitmap = AppKit.NSBitmapImageRep.alloc().initWithCGImage_(image)
        png = bytes(bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}))
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
        handler.performRequests_error_([request], None)
        texts = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if candidates:
                texts.append(str(candidates[0].string()))
        urls = []
        if self.article_url_log.exists():
            urls = list(dict.fromkeys(
                line.strip() for line in self.article_url_log.read_text(encoding="utf-8").splitlines()
                if line.startswith("https://mp.weixin.qq.com/s?")
            ))
        return Viewport(
            fingerprint=hashlib.sha256(png).hexdigest(),
            article_urls=urls,
            at_verified_bottom=any(marker in " ".join(texts) for marker in self.BOTTOM_MARKERS),
        )

    def scroll(self) -> None:  # pragma: no cover - requires an interactive Mac desktop
        import Quartz
        event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 1, -700)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.8)

    def open_next(self) -> None:
        return None

    def close_detail(self) -> None:  # pragma: no cover - requires an interactive Mac desktop
        import Quartz
        down = Quartz.CGEventCreateKeyboardEvent(None, 53, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, 53, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
