from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .audit import audit_workspace
from .exports import export_all
from .models import Account, ArticleSeed, ContentSnapshot, MetricSnapshot, Status
from .storage import Store
from .urls import article_stable_key, normalize_url
from .workspace import Workspace

Progress = Callable[[str, dict], None]


def _silent(stage: str, detail: dict) -> None:
    del stage, detail


@dataclass(slots=True)
class Window:
    number: int
    title: str
    x: float
    y: float
    width: float
    height: float
    layer: int
    pid: int


@dataclass(slots=True)
class OcrLine:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2


@dataclass(slots=True)
class ProfileCard:
    title: str
    read_num: int | None
    like_num: int | None
    click_x: float
    click_y: float


@dataclass(slots=True)
class HumanCapture:
    url: str
    title: str
    body: str
    read_num: int | None
    like_num: int | None
    share_num: int | None
    comment_num: int | None


COUNT_RE = re.compile(r"([\d.]+)\s*([万萬wW]?)")
METRIC_RE = re.compile(r"阅读\s*([\d.]+\s*[万萬wW]?)\s*赞\s*([\d.]+\s*[万萬wW]?)")


def parse_count(value: str) -> int | None:
    match = COUNT_RE.search(value.replace(",", ""))
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2):
        number *= 10_000
    return int(number)


def parse_share_num(text: str) -> int | None:
    for pattern in (r"转发\s*([\d.]+\s*[万萬wW]?)", r"分享\s*([\d.]+\s*[万萬wW]?)"):
        match = re.search(pattern, text)
        if match:
            return parse_count(match.group(1))
    return None


def _is_noise(text: str) -> bool:
    value = text.strip()
    return bool(
        not value
        or value in {"公众号", "全部", "文章", "原创", "关注", "发消息"}
        or re.fullmatch(r"[·•…\-—_]+", value)
        or re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", value)
        or METRIC_RE.search(value)
        or "篇原创内容" in value
    )


def parse_profile_cards(lines: list[OcrLine], window_height: float | None = None) -> list[ProfileCard]:
    ordered = sorted(lines, key=lambda item: item.cy)
    cards: list[ProfileCard] = []
    last_metric_y = -1.0
    for index, line in enumerate(ordered):
        match = METRIC_RE.search(line.text.replace(" ", ""))
        if not match:
            continue
        candidates: list[OcrLine] = []
        for previous in reversed(ordered[:index]):
            if previous.cy <= last_metric_y + 4 or line.cy - previous.cy > 130:
                break
            if _is_noise(previous.text):
                continue
            if candidates and candidates[-1].cy - previous.cy > 34:
                break
            candidates.append(previous)
            if len(candidates) >= 2 or line.cy - previous.cy > 85:
                break
        candidates.reverse()
        title = "".join(item.text.strip() for item in candidates).strip()
        if not title or ("全部" in title and "文章" in title):
            last_metric_y = line.cy
            continue
        click_y = line.cy
        if window_height is not None and click_y > window_height - 75:
            last_metric_y = line.cy
            continue
        cards.append(ProfileCard(
            title=title,
            read_num=parse_count(match.group(1)),
            like_num=parse_count(match.group(2)),
            click_x=line.cx,
            click_y=click_y,
        ))
        last_metric_y = line.cy
    return cards


def account_name_from_profile(lines: list[OcrLine]) -> str:
    ordered = sorted(lines, key=lambda item: item.cy)
    for index, line in enumerate(ordered):
        if "篇原创内容" not in line.text:
            continue
        header_candidates = [
            previous for previous in ordered[:index]
            if 35 <= previous.cy <= 120
        ]
        for previous in header_candidates:
            value = previous.text.strip()
            if (
                1 < len(value) <= 30
                and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", value)
                and not _is_noise(value)
                and " " not in value
            ):
                return value
    for line in ordered:
        value = line.text.strip()
        if (
            1 < len(value) <= 30
            and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", value)
            and not _is_noise(value)
            and not re.search(r"阅读|赞|\d{4}年", value)
        ):
            return value
    return "未知公众号"


class MacHumanController:
    def __init__(self) -> None:
        if subprocess.run(
            ["uname", "-s"], capture_output=True, text=True, check=False
        ).stdout.strip() != "Darwin":
            raise RuntimeError("Mac 微信人工模拟器只能在 macOS 运行")
        import AppKit
        import ApplicationServices
        import Quartz
        import Vision

        self.AppKit = AppKit
        self.AX = ApplicationServices
        self.Quartz = Quartz
        self.Vision = Vision

    def activate(self) -> None:
        subprocess.run(["open", "-a", "WeChat"], check=False)
        time.sleep(0.6)

    def windows(self) -> list[Window]:
        q = self.Quartz
        rows = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly, q.kCGNullWindowID)
        result: list[Window] = []
        for row in rows:
            owner = str(row.get(q.kCGWindowOwnerName, ""))
            if owner not in {"微信", "WeChat"}:
                continue
            bounds = row.get(q.kCGWindowBounds, {})
            result.append(Window(
                number=int(row.get(q.kCGWindowNumber, 0)),
                title=str(row.get(q.kCGWindowName, "")),
                x=float(bounds.get("X", 0)), y=float(bounds.get("Y", 0)),
                width=float(bounds.get("Width", 0)), height=float(bounds.get("Height", 0)),
                layer=int(row.get(q.kCGWindowLayer, 0)),
                pid=int(row.get(q.kCGWindowOwnerPID, 0)),
            ))
        return result

    def window(self, title: str, *, min_width: float = 200) -> Window | None:
        matches = [item for item in self.windows() if item.title == title and item.width >= min_width]
        return min(matches, key=lambda item: (item.layer, -item.width)) if matches else None

    def wait_window(self, title: str, timeout: float = 8) -> Window:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.window(title)
            if found:
                return found
            time.sleep(0.2)
        raise RuntimeError(f"没有找到微信窗口：{title}")

    def screenshot(self, window: Window):
        q = self.Quartz
        return q.CGWindowListCreateImage(
            q.CGRectNull,
            q.kCGWindowListOptionIncludingWindow,
            window.number,
            q.kCGWindowImageBoundsIgnoreFraming,
        )

    def ocr(self, window: Window) -> list[OcrLine]:
        image = self.screenshot(window)
        if image is None:
            return []
        request = self.Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(self.Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])
        request.setUsesLanguageCorrection_(True)
        handler = self.Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"OCR 失败：{error}")
        pixel_w = float(self.Quartz.CGImageGetWidth(image))
        pixel_h = float(self.Quartz.CGImageGetHeight(image))
        sx = window.width / pixel_w
        sy = window.height / pixel_h
        lines: list[OcrLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            box = observation.boundingBox()
            lines.append(OcrLine(
                text=str(candidates[0].string()),
                x=float(box.origin.x * pixel_w * sx),
                y=float((1 - box.origin.y - box.size.height) * pixel_h * sy),
                width=float(box.size.width * pixel_w * sx),
                height=float(box.size.height * pixel_h * sy),
            ))
        return sorted(lines, key=lambda item: (item.y, item.x))

    def click(self, x: float, y: float) -> None:
        q = self.Quartz
        point = (x, y)
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, point, q.kCGMouseButtonLeft))
        time.sleep(0.05)
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseDown, point, q.kCGMouseButtonLeft))
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseUp, point, q.kCGMouseButtonLeft))

    def drag(self, x: float, start_y: float, end_y: float) -> None:
        q = self.Quartz
        start = (x, start_y)
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, start, q.kCGMouseButtonLeft))
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseDown, start, q.kCGMouseButtonLeft))
        for step in range(1, 13):
            y = start_y + (end_y - start_y) * step / 12
            q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseDragged, (x, y), q.kCGMouseButtonLeft))
            time.sleep(0.015)
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventLeftMouseUp, (x, end_y), q.kCGMouseButtonLeft))

    def hotkey(self, keycode: int, flags: int) -> None:
        q = self.Quartz
        down = q.CGEventCreateKeyboardEvent(None, keycode, True)
        q.CGEventSetFlags(down, flags)
        up = q.CGEventCreateKeyboardEvent(None, keycode, False)
        q.CGEventSetFlags(up, flags)
        q.CGEventPost(q.kCGHIDEventTap, down)
        q.CGEventPost(q.kCGHIDEventTap, up)

    def _raise(self, target: Window) -> bool:
        ax = self.AX
        app = ax.AXUIElementCreateApplication(target.pid)
        error, windows = ax.AXUIElementCopyAttributeValue(app, ax.kAXWindowsAttribute, None)
        if error != 0 or not windows:
            return False
        for item in windows:
            _, title = ax.AXUIElementCopyAttributeValue(item, ax.kAXTitleAttribute, None)
            if str(title or "") != target.title:
                continue
            if ax.AXUIElementPerformAction(item, ax.kAXRaiseAction) == 0:
                time.sleep(0.25)
                return True
        return False

    def focus_profile(self) -> Window:
        if self.window("公众号") is None:
            self.activate()
        for viewer in [item for item in self.windows() if item.title == "图片和视频"]:
            self._raise(viewer)
            self.hotkey(13, self.Quartz.kCGEventFlagMaskCommand)
            time.sleep(0.2)
        profile = self.wait_window("公众号")
        self._raise(profile)
        return self.wait_window("公众号")

    def focus_browser(self) -> Window:
        if self.window("微信 (窗口)") is None:
            self.activate()
        browser = self.wait_window("微信 (窗口)")
        self._raise(browser)
        return self.wait_window("微信 (窗口)")

    def clipboard(self) -> str:
        board = self.AppKit.NSPasteboard.generalPasteboard()
        return str(board.stringForType_(self.AppKit.NSPasteboardTypeString) or "")

    def copy_page_text(self) -> str:
        browser = self.focus_browser()
        self.click(browser.x + browser.width / 2, browser.y + min(180, browser.height / 3))
        time.sleep(0.15)
        self.hotkey(0, self.Quartz.kCGEventFlagMaskCommand)
        self.hotkey(8, self.Quartz.kCGEventFlagMaskCommand)
        time.sleep(0.4)
        return self.clipboard()

    def copy_link(self) -> str:
        browser = self.focus_browser()
        self.hotkey(53, 0)
        time.sleep(0.25)
        browser = self.focus_browser()
        self.click(browser.x + browser.width - 28, browser.y + 21)
        time.sleep(0.3)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            menus = [item for item in self.windows() if item.layer > 0 and item.height > 180]
            for menu in menus:
                for line in self.ocr(menu):
                    if "复制链接" in line.text:
                        self.click(menu.x + line.cx, menu.y + line.cy)
                        for _ in range(20):
                            value = self.clipboard().strip()
                            if value.startswith("https://mp.weixin.qq.com/"):
                                return normalize_url(value)
                            time.sleep(0.1)
            time.sleep(0.15)
        raise RuntimeError("文章菜单未能复制真实链接")

    def page_bottom(self) -> str:
        self.focus_browser()
        self.hotkey(125, self.Quartz.kCGEventFlagMaskCommand)
        time.sleep(0.6)
        return "\n".join(line.text for line in self.ocr(self.focus_browser()))

    def close_article(self) -> None:
        try:
            self.focus_browser()
            self.hotkey(13, self.Quartz.kCGEventFlagMaskCommand)
            time.sleep(0.35)
        except RuntimeError:
            pass

    def scroll_to_top(self) -> Window:
        profile = self.focus_profile()
        self.click(profile.x + 24, profile.y + min(profile.height - 100, profile.height / 2))
        self.hotkey(126, self.Quartz.kCGEventFlagMaskCommand)
        time.sleep(0.7)
        return self.focus_profile()

    def _scrollbar_thumb(self, profile: Window) -> tuple[float, float] | None:
        image = self.screenshot(profile)
        if image is None:
            return None
        q = self.Quartz
        width = q.CGImageGetWidth(image)
        height = q.CGImageGetHeight(image)
        provider = q.CGImageGetDataProvider(image)
        raw = bytes(q.CGDataProviderCopyData(provider))
        row_bytes = q.CGImageGetBytesPerRow(image)
        bytes_per_pixel = max(1, q.CGImageGetBitsPerPixel(image) // 8)
        runs: list[tuple[int, int]] = []
        for px in range(max(0, width - 16), width - 2):
            start: int | None = None
            local: list[tuple[int, int]] = []
            for py in range(70, height - 40):
                offset = py * row_bytes + px * bytes_per_pixel
                values = raw[offset: offset + min(3, bytes_per_pixel)]
                gray = len(values) >= 3 and max(values) - min(values) < 12 and 110 <= values[0] <= 220
                if gray and start is None:
                    start = py
                elif not gray and start is not None:
                    if py - start >= 20:
                        local.append((start, py - 1))
                    start = None
            runs.extend(local)
        if not runs:
            return None
        start, end = max(runs, key=lambda item: item[1] - item[0])
        sy = profile.height / height
        return start * sy, end * sy

    def scroll(self, amount: int = 1) -> bool:
        profile = self.focus_profile()
        self.click(profile.x + profile.width - 5, profile.y + profile.height / 2)
        time.sleep(0.18)
        thumb = self._scrollbar_thumb(profile)
        if thumb:
            center = (thumb[0] + thumb[1]) / 2
            step = max(60, min(110, 85 * max(1, abs(amount))))
            target = min(profile.height - 40, center + step)
            if target <= center + 2:
                return False
            self.drag(profile.x + profile.width - 5, profile.y + center, profile.y + target)
            time.sleep(0.65)
            return True
        self.click(profile.x + 25, profile.y + profile.height / 2)
        for _ in range(max(1, abs(amount))):
            self.hotkey(121, 0)
        time.sleep(0.65)
        return True


class MacHumanAccountCollector:
    def __init__(self, progress: Progress = _silent, controller: MacHumanController | None = None):
        self.progress = progress
        self.controller = controller or MacHumanController()

    @staticmethod
    def _target_count(lines: list[OcrLine]) -> int | None:
        for line in lines:
            match = re.search(r"(\d+)\s*篇原创内容", line.text)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _previous_total(output: Path, account_name: str) -> int | None:
        path = output.expanduser() / account_name / "audit" / "human-agent-progress.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return int(value.get("declared_article_count") or 0) or None
        except (OSError, ValueError, TypeError):
            return None

    def _capture_card(self, profile: Window, card: ProfileCard) -> HumanCapture:
        self.controller.click(profile.x + card.click_x, profile.y + card.click_y)
        self.controller.wait_window("微信 (窗口)", timeout=7)
        time.sleep(0.5)
        try:
            url = self.controller.copy_link()
            body = self.controller.copy_page_text()
            bottom = self.controller.page_bottom()
            combined = body + "\n" + bottom
            comment_num = None
            if "暂无评论" in combined or "还没有留言" in combined:
                comment_num = 0
            else:
                match = re.search(r"(?:留言|评论)\s*([\d.]+\s*[万萬wW]?)", combined)
                if match:
                    comment_num = parse_count(match.group(1))
            read_match = re.search(r"阅读\s*([\d.]+\s*[万萬wW]?)", bottom)
            return HumanCapture(
                url=url, title=card.title, body=body,
                read_num=parse_count(read_match.group(1)) if read_match else card.read_num,
                like_num=card.like_num,
                share_num=parse_share_num(bottom), comment_num=comment_num,
            )
        finally:
            self.controller.close_article()
            self.controller.focus_profile()

    def _save(self, ws: Workspace, store: Store, account: Account, capture: HumanCapture) -> None:
        key = article_stable_key(url=capture.url)
        article = ArticleSeed(
            stable_key=key, url=capture.url, biz=account.biz, title=capture.title,
            source="mac_human_agent", status=Status.OK,
        )
        store.upsert_article(article)
        raw_name = key.replace(":", "_")
        md_path = f"raw/markdown/{raw_name}.md"
        raw_path = f"raw/human-agent/{raw_name}.json"
        (ws.root / md_path).parent.mkdir(parents=True, exist_ok=True)
        (ws.root / md_path).write_text(capture.body, encoding="utf-8")
        ws.write_json(raw_path, {
            "url": capture.url, "title": capture.title, "body": capture.body,
            "readNum": capture.read_num, "likeNum": capture.like_num,
            "shareNum": capture.share_num, "commentNum": capture.comment_num,
            "source": "mac_human_agent",
        })
        store.save_content(ContentSnapshot(
            article_key=key, title=capture.title, author=account.name,
            markdown=capture.body, source="mac_human_agent",
            checksum=hashlib.sha256(capture.body.encode()).hexdigest(),
            status=Status.OK if capture.body.strip() else Status.MISSING,
            reason="" if capture.body.strip() else "微信页面未复制到正文",
        ), markdown_path=md_path)
        metric_status = Status.OK if capture.read_num is not None else Status.MISSING
        store.save_metrics(MetricSnapshot(
            article_key=key, readNum=capture.read_num, likeNum=capture.like_num,
            shareNum=capture.share_num, commentNum=capture.comment_num,
            source="mac_human_agent",
            checksum=hashlib.sha256(json.dumps({
                "readNum": capture.read_num, "likeNum": capture.like_num,
                "shareNum": capture.share_num, "commentNum": capture.comment_num,
            }, sort_keys=True).encode()).hexdigest(),
            status=metric_status,
            reason="" if metric_status == Status.OK else "公众号主页未识别到阅读数",
        ))
        store.save_comments(
            key, [], Status.OK if capture.comment_num == 0 else Status.MISSING,
            "" if capture.comment_num == 0 else "桌面页面只能确认公开评论计数，评论明细尚未展开",
        )

    def collect(self, url: str, output: Path, *, max_articles: int | None = None) -> Path:
        self.controller.activate()
        profile = self.controller.scroll_to_top()
        lines = self.controller.ocr(profile)
        account_name = account_name_from_profile(lines)
        total = self._target_count(lines) or self._previous_total(output, account_name)
        if not total:
            raise RuntimeError("未从公众号主页识别到文章总数，也没有可恢复的历史进度")
        if max_articles:
            total = min(total, max_articles)
        account = Account(
            biz="human:" + hashlib.sha256(account_name.encode()).hexdigest()[:20],
            name=account_name, source_url=url, source="mac_human_agent", status=Status.OK,
        )
        ws = Workspace.create(output, account_name)
        store = Store(ws.database)
        store.upsert_account(account)
        completed_urls = {
            row["url"] for row in store.rows(
                """SELECT DISTINCT a.url FROM articles a
                   JOIN content_snapshots c ON c.article_key=a.stable_key AND c.status='ok'
                   JOIN metric_snapshots m ON m.article_key=a.stable_key AND m.readNum IS NOT NULL"""
            )
        }
        seen_hints: set[str] = set()
        repeated = 0
        previous_fingerprint = ""
        attempts = 0
        while len(completed_urls) < total and repeated < 8:
            profile = self.controller.focus_profile()
            lines = self.controller.ocr(profile)
            cards = parse_profile_cards(lines, profile.height)
            fingerprint = hashlib.sha256("\n".join(card.title for card in cards).encode()).hexdigest()
            repeated = repeated + 1 if fingerprint == previous_fingerprint else 0
            previous_fingerprint = fingerprint
            new_hint = False
            for card in cards:
                hint = f"{card.title}|{card.read_num}|{card.like_num}"
                if hint in seen_hints:
                    continue
                seen_hints.add(hint)
                new_hint = True
                attempts += 1
                self.progress("human_article", {
                    "current": len(completed_urls) + 1, "total": total, "title": card.title,
                })
                try:
                    capture = self._capture_card(profile, card)
                    if capture.url in completed_urls:
                        continue
                    self._save(ws, store, account, capture)
                    completed_urls.add(capture.url)
                except (RuntimeError, OSError, ValueError) as exc:
                    self.progress("human_retry", {
                        "title": card.title,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    try:
                        self.controller.close_article()
                    except RuntimeError:
                        pass
                ws.write_json("audit/human-agent-progress.json", {
                    "account": account_name,
                    "declared_article_count": total,
                    "captured_article_count": len(completed_urls),
                    "attempts": attempts,
                    "complete": len(completed_urls) >= total,
                })
                if len(completed_urls) >= total:
                    break
                profile = self.controller.focus_profile()
            if len(completed_urls) >= total:
                break
            if not self.controller.scroll():
                break
            if not new_hint:
                repeated += 1
        fingerprint = hashlib.sha256("\n".join(sorted(completed_urls)).encode()).hexdigest()
        store.set_checkpoint("mac_human_agent", account.biz, str(len(completed_urls)), fingerprint,
                             len(completed_urls) >= total)
        export_all(store, ws.root)
        audit_workspace(store, ws.root)
        ws.write_json("audit/human-agent-progress.json", {
            "account": account_name,
            "declared_article_count": total,
            "captured_article_count": len(completed_urls),
            "attempts": attempts,
            "complete": len(completed_urls) >= total,
        })
        self.progress("complete", {"workspace": str(ws.root), "captured": len(completed_urls), "total": total})
        return ws.root
