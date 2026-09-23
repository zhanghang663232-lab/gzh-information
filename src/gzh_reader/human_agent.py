from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .audit import audit_workspace
from .exports import export_all
from .models import Account, ArticleSeed, ContentSnapshot, MetricSnapshot, Status
from .storage import Store
from .urls import article_stable_key, normalize_url
from .workspace import Workspace

if TYPE_CHECKING:
    from .deepseek import DeepSeekCardReviewer

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


def card_signature(card: ProfileCard) -> tuple[str, int | None, int | None]:
    """Conservative resume hint; never an article identity or completion proof."""
    return (card.title.strip(), card.read_num, card.like_num)


def unique_known_cards(rows: list[dict]) -> dict[tuple[str, int | None, int | None], str]:
    matches: dict[tuple[str, int | None, int | None], set[str]] = {}
    for row in rows:
        signature = (str(row["title"] or "").strip(), row["readNum"], row["likeNum"])
        if signature[0]:
            matches.setdefault(signature, set()).add(str(row["url"]))
    return {signature: next(iter(urls)) for signature, urls in matches.items() if len(urls) == 1}


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


def profile_matches_account(lines: list[OcrLine], account_name: str) -> bool:
    texts = {line.text.strip() for line in lines}
    return account_name in texts and any("篇原创内容" in text for text in texts)


def profile_identity_conflicts(lines: list[OcrLine], account_name: str) -> bool:
    """Only judge identity when the profile header is actually visible."""
    return any("篇原创内容" in line.text for line in lines) and not profile_matches_account(
        lines, account_name
    )


class AccountMismatchError(RuntimeError):
    """A clicked article belongs to another account; discard the viewport."""


def article_matches_account(bottom_text: str, account_name: str) -> bool:
    return account_name in {line.strip() for line in bottom_text.splitlines()}


def meaningful_body(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    non_urls = [line for line in lines if not re.fullmatch(r"https?://\S+", line)]
    return len("".join(non_urls)) >= 80


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
        # Reopen moves an existing WeChat window back to the active macOS Space.
        # NSRunningApplication.activateWithOptions_ alone may leave it off-screen.
        subprocess.run(
            ["osascript", "-e", 'tell application "WeChat" to reopen',
             "-e", 'tell application "WeChat" to activate'],
            capture_output=True, check=False, timeout=8,
        )
        apps = self.AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            "com.tencent.xinWeChat"
        )
        options = (
            self.AppKit.NSApplicationActivateAllWindows
            | self.AppKit.NSApplicationActivateIgnoringOtherApps
        )
        for app in apps:
            app.activateWithOptions_(options)
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
        # Vision occasionally blocks indefinitely inside performRequests_error_.
        # Keep it in a short-lived child process so a stuck OCR call cannot
        # strand the whole collection run or prevent its checkpoint/export.
        try:
            result = subprocess.run(
                [sys.executable, "-m", "gzh_reader.ocr_worker",
                 str(window.number), str(window.width), str(window.height)],
                capture_output=True, text=True, check=False, timeout=12,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("微信窗口 OCR 超时（12 秒）；本篇跳过并保留断点") from exc
        if result.returncode != 0:
            raise RuntimeError(
                "微信窗口 OCR 失败：" + (result.stderr.strip()[-300:] or str(result.returncode))
            )
        try:
            payload = json.loads(result.stdout)
            lines = [OcrLine(**item) for item in payload["lines"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("微信窗口 OCR 返回了无效结果") from exc
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

    def wheel(self, x: float, y: float, delta: int) -> None:
        q = self.Quartz
        q.CGEventPost(
            q.kCGHIDEventTap,
            q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, (x, y), q.kCGMouseButtonLeft),
        )
        for _ in range(4):
            event = q.CGEventCreateScrollWheelEvent(
                None, q.kCGScrollEventUnitPixel, 1, int(delta / 4)
            )
            q.CGEventPost(q.kCGHIDEventTap, event)
            time.sleep(0.06)

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
            self._close_window(viewer)
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

    def _close_window(self, target: Window) -> None:
        # Command-W goes to the global foreground window. Never send it if
        # Accessibility could not raise the exact child window first.
        if not self._raise(target):
            raise RuntimeError(f"无法确认 {target.title} 窗口焦点，已拒绝关闭快捷键")
        self.hotkey(13, self.Quartz.kCGEventFlagMaskCommand)

    def open_profile(self, url: str, account_name: str | None = None) -> Window:
        """Restore the account profile from a public article in WeChat itself."""
        def account_lines(lines: list[OcrLine]) -> list[OcrLine]:
            if account_name:
                return [line for line in lines if line.text.strip() == account_name]
            metric_lines = [
                line for line in lines
                if "写留言" in line.text or "推荐" in line.text or "点赞" in line.text
            ]
            if not metric_lines:
                return []
            anchor = max(line.cy for line in metric_lines)
            return [
                line for line in lines
                if abs(line.cy - anchor) < 35
                and 1 < len(line.text.strip()) <= 30
                and re.search(r"[\u4e00-\u9fff]", line.text)
                and not any(word in line.text for word in ("写留言", "推荐", "点赞", "分享"))
            ]

        existing = self.window("公众号")
        if existing:
            self._raise(existing)
            if account_name is not None and profile_matches_account(
                self.ocr(existing), account_name
            ):
                return existing
            self._close_window(existing)
            time.sleep(0.8)
        self.activate()
        browser = self.window("微信 (窗口)")
        if browser is not None:
            self._close_window(browser)
            time.sleep(0.8)
        main = self.wait_window("微信")
        self._raise(main)
        marker = url.rstrip("/").rsplit("/", 1)[-1]
        links = [
            line for line in self.ocr(main)
            if "mp.weixin.qq.com" in line.text and marker in line.text
        ]
        if not links:
            raise RuntimeError("微信主窗口中没有看到用于恢复的示例文章链接")
        line = links[-1]
        self.click(main.x + line.cx, main.y + line.cy)
        browser = self.wait_window("微信 (窗口)", timeout=10)
        time.sleep(1)
        self._raise(browser)
        lines = self.ocr(self.focus_browser())
        names = account_lines(lines)
        if not names:
            self.page_bottom()
            browser = self.focus_browser()
            names = account_lines(self.ocr(browser))
        if not names:
            raise RuntimeError("示例文章页中没有找到目标公众号名称")
        line = names[-1]
        self.click(browser.x + line.cx, browser.y + line.cy)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            profile = self.window("公众号")
            if profile:
                lines = self.ocr(profile)
                ready = (
                    profile_matches_account(lines, account_name)
                    if account_name
                    else account_name_from_profile(lines) != "未知公众号"
                    and any("篇原创内容" in item.text for item in lines)
                )
                if ready:
                    return profile
            time.sleep(0.4)
        raise RuntimeError("目标公众号主页未通过账号身份校验")

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
        self.AppKit.NSPasteboard.generalPasteboard().clearContents()
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
        browser = self.window("微信 (窗口)")
        if browser is None:
            return
        self._close_window(browser)
        time.sleep(0.35)

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
        # A real wheel/trackpad event is required to make WeChat's virtualized
        # article list request its next batch.  Dragging the temporary
        # scrollbar thumb or pressing PageDown can stop at the loaded boundary.
        self.wheel(
            profile.x + profile.width / 2,
            profile.y + profile.height * 0.72,
            360 * max(1, abs(amount)),
        )
        time.sleep(0.9)
        return True


class MacHumanAccountCollector:
    def __init__(
        self, progress: Progress = _silent, controller: MacHumanController | None = None,
        reviewer: DeepSeekCardReviewer | None = None,
    ):
        self.progress = progress
        self.controller = controller or MacHumanController()
        self.reviewer = reviewer

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

    def _capture_card(
        self, profile: Window, card: ProfileCard, expected_account: str
    ) -> HumanCapture:
        self.controller.click(profile.x + card.click_x, profile.y + card.click_y)
        self.controller.wait_window("微信 (窗口)", timeout=7)
        time.sleep(0.5)
        try:
            url = self.controller.copy_link()
            body = self.controller.copy_page_text()
            bottom = self.controller.page_bottom()
            if not article_matches_account(bottom, expected_account):
                raise AccountMismatchError(
                    f"文章作者不匹配：期望 {expected_account}，已拒绝入库"
                )
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
            try:
                self.controller.focus_profile()
            except RuntimeError:
                # A WeChat child window occasionally closes its parent profile
                # as well.  The caller restores it from the seed article.
                pass

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
        body_ok = meaningful_body(capture.body)
        store.save_content(ContentSnapshot(
            article_key=key, title=capture.title, author=account.name,
            markdown=capture.body, source="mac_human_agent",
            checksum=hashlib.sha256(capture.body.encode()).hexdigest(),
            status=Status.OK if body_ok else Status.MISSING,
            reason="" if body_ok else "微信页面未复制到有效正文",
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

    def collect(
        self, url: str, output: Path, *, max_articles: int | None = None,
        account_name: str | None = None, max_new_articles: int = 5,
    ) -> Path:
        if not 1 <= max_new_articles <= 10:
            raise ValueError("本轮最多新增篇数必须在 1 到 10 之间")
        self.controller.activate()
        self.controller.open_profile(url, account_name)
        profile = self.controller.scroll_to_top()
        lines = self.controller.ocr(profile)
        observed_name = account_name_from_profile(lines)
        if account_name is not None and observed_name != account_name:
            raise AccountMismatchError(
                f"公众号主页不匹配：期望 {account_name}，看到 {observed_name}"
            )
        account_name = observed_name
        total = self._target_count(lines) or self._previous_total(output, account_name)
        if not total:
            raise RuntimeError("未从公众号主页识别到文章总数，也没有可恢复的历史进度")
        if max_articles is not None and max_articles < 1:
            raise ValueError("max_articles 必须大于零")
        account = Account(
            biz="human:" + hashlib.sha256(account_name.encode()).hexdigest()[:20],
            name=account_name, source_url=url, source="mac_human_agent", status=Status.OK,
        )
        ws = Workspace.create(output, account_name)
        store = Store(ws.database)
        store.upsert_account(account)
        completed_rows = store.rows(
            """SELECT a.url, a.title, m.readNum, m.likeNum FROM articles a
               JOIN metric_snapshots m ON m.id = (
                 SELECT MAX(m2.id) FROM metric_snapshots m2
                 WHERE m2.article_key=a.stable_key AND m2.readNum IS NOT NULL)
               WHERE a.biz=? AND EXISTS (
                 SELECT 1 FROM content_snapshots c
                 WHERE c.article_key=a.stable_key AND c.status='ok')""",
            (account.biz,),
        )
        completed_urls = {row["url"] for row in completed_rows}
        known_cards = unique_known_cards(completed_rows)
        ambiguous_cards: set[tuple[str, int | None, int | None]] = set()
        hints_path = ws.root / "audit" / "human-agent-card-hints.json"
        try:
            hints_payload = json.loads(hints_path.read_text(encoding="utf-8"))
            card_hints = hints_payload.get("cards", {}) if hints_payload.get("biz") == account.biz else {}
            if not isinstance(card_hints, dict):
                card_hints = {}
        except (OSError, ValueError, TypeError, AttributeError):
            card_hints = {}
        seen_hints: set[str] = set()
        repeated = 0
        previous_fingerprint = ""
        attempts = 0
        new_in_run = 0
        skipped_known = 0
        consecutive_failures = 0
        last_open_at = 0.0
        max_open_attempts = 3 * max_new_articles
        identity_recoveries = 0
        stop_reason = "viewport_repeated_without_new_articles"
        halt = False
        profile_number = profile.number
        while (
            repeated < 30
            and identity_recoveries < 3
            and (max_articles is None or len(completed_urls) < max_articles)
            and new_in_run < max_new_articles
            and attempts < max_open_attempts
        ):
            try:
                profile = self.controller.focus_profile()
            except RuntimeError:
                try:
                    self.controller.open_profile(url, account_name)
                    profile = self.controller.scroll_to_top()
                except RuntimeError as exc:
                    stop_reason = "wechat_session_unavailable"
                    self.progress("session_unavailable", {"error": str(exc)})
                    break
                profile_number = profile.number
                previous_fingerprint = ""
                repeated = 0
            try:
                lines = self.controller.ocr(profile)
            except RuntimeError as exc:
                stop_reason = "ocr_unavailable"
                self.progress("human_retry", {"error": f"{type(exc).__name__}: {exc}"})
                break
            if profile.number != profile_number or profile_identity_conflicts(lines, account_name):
                identity_recoveries += 1
                self.progress("identity_mismatch", {
                    "expected": account_name,
                    "observed": account_name_from_profile(lines),
                })
                try:
                    self.controller.open_profile(url, account_name)
                    profile = self.controller.scroll_to_top()
                except RuntimeError as exc:
                    stop_reason = "wechat_session_unavailable"
                    self.progress("session_unavailable", {"error": str(exc)})
                    break
                profile_number = profile.number
                previous_fingerprint = ""
                repeated = 0
                continue
            cards = parse_profile_cards(lines, profile.height)
            if not cards and self.reviewer is not None:
                try:
                    cards = self.reviewer.review_cards(lines, profile.height)
                    if cards:
                        self.progress("model_review", {"cards": len(cards), "calls": self.reviewer.calls})
                except RuntimeError as exc:
                    self.progress("model_review_failed", {"error": str(exc)})
            fingerprint = hashlib.sha256("\n".join(
                card.title for card in cards
            ).encode()).hexdigest()
            repeated = repeated + 1 if fingerprint == previous_fingerprint else 0
            previous_fingerprint = fingerprint
            restart_profile = False
            card_counts = Counter(card_signature(card) for card in cards)
            for index, card in enumerate(cards):
                if attempts >= max_open_attempts or new_in_run >= max_new_articles:
                    break
                hint = f"{fingerprint}|{index}"
                if hint in seen_hints:
                    continue
                seen_hints.add(hint)
                signature = card_signature(card)
                known_url = card_hints.get(hint) or (
                    known_cards.get(signature) if card_counts[signature] == 1 else None
                )
                if known_url in completed_urls:
                    skipped_known += 1
                    continue
                since_open = time.monotonic() - last_open_at
                if last_open_at and since_open < 8:
                    time.sleep(8 - since_open)
                attempts += 1
                last_open_at = time.monotonic()
                self.progress("human_article", {
                    "current": len(completed_urls) + 1, "total": total, "title": card.title,
                    "opens_in_run": attempts, "max_opens": max_open_attempts,
                })
                try:
                    capture = self._capture_card(profile, card, account_name)
                    card_hints[hint] = capture.url
                    ws.write_json("audit/human-agent-card-hints.json", {
                        "biz": account.biz, "cards": card_hints,
                    })
                    if capture.url not in completed_urls:
                        self._save(ws, store, account, capture)
                        completed_urls.add(capture.url)
                        new_in_run += 1
                        if known_cards.get(signature) not in (None, capture.url):
                            known_cards.pop(signature, None)
                            ambiguous_cards.add(signature)
                        elif signature not in ambiguous_cards:
                            known_cards[signature] = capture.url
                    consecutive_failures = 0
                    identity_recoveries = 0
                except AccountMismatchError as exc:
                    identity_recoveries += 1
                    self.progress("identity_mismatch", {
                        "expected": account_name, "observed": str(exc),
                    })
                    try:
                        self.controller.open_profile(url, account_name)
                        profile = self.controller.scroll_to_top()
                    except RuntimeError as restore_error:
                        stop_reason = "wechat_session_unavailable"
                        self.progress("session_unavailable", {"error": str(restore_error)})
                        halt = True
                        break
                    profile_number = profile.number
                    previous_fingerprint = ""
                    repeated = 0
                    restart_profile = True
                    break
                except (RuntimeError, OSError, ValueError) as exc:
                    consecutive_failures += 1
                    self.progress("human_retry", {
                        "title": card.title,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    try:
                        self.controller.close_article()
                    except RuntimeError:
                        pass
                    if consecutive_failures >= 2:
                        stop_reason = "consecutive_article_failures"
                        halt = True
                ws.write_json("audit/human-agent-progress.json", {
                    "account": account_name,
                    "declared_article_count": total,
                    "captured_article_count": len(completed_urls),
                    "attempts": attempts,
                    "new_in_run": new_in_run,
                    "skipped_known_cards": skipped_known,
                    "complete": False,
                })
                if halt or new_in_run >= max_new_articles or attempts >= max_open_attempts or (
                    max_articles is not None and len(completed_urls) >= max_articles
                ):
                    break
                try:
                    profile = self.controller.focus_profile()
                except RuntimeError:
                    try:
                        self.controller.open_profile(url, account_name)
                        profile = self.controller.scroll_to_top()
                    except RuntimeError as exc:
                        stop_reason = "wechat_session_unavailable"
                        self.progress("session_unavailable", {"error": str(exc)})
                        halt = True
                        break
                    profile_number = profile.number
                    previous_fingerprint = ""
                    repeated = 0
                    restart_profile = True
                    break
                try:
                    profile_lines = self.controller.ocr(profile)
                except RuntimeError as exc:
                    stop_reason = "ocr_unavailable"
                    self.progress("human_retry", {"error": f"{type(exc).__name__}: {exc}"})
                    halt = True
                    break
                if profile.number != profile_number or profile_identity_conflicts(
                    profile_lines, account_name
                ):
                    identity_recoveries += 1
                    self.progress("identity_mismatch", {
                        "expected": account_name,
                        "observed": "其他公众号",
                    })
                    try:
                        self.controller.open_profile(url, account_name)
                        profile = self.controller.scroll_to_top()
                    except RuntimeError as exc:
                        stop_reason = "wechat_session_unavailable"
                        self.progress("session_unavailable", {"error": str(exc)})
                        halt = True
                        break
                    profile_number = profile.number
                    previous_fingerprint = ""
                    repeated = 0
                    restart_profile = True
                    break
            if max_articles is not None and len(completed_urls) >= max_articles:
                stop_reason = "article_limit_reached"
                break
            if halt:
                break
            if new_in_run >= max_new_articles:
                stop_reason = "batch_new_limit"
                break
            if attempts >= max_open_attempts:
                stop_reason = "batch_open_limit"
                break
            if restart_profile:
                continue
            try:
                scrolled = self.controller.scroll()
            except RuntimeError as exc:
                stop_reason = "wechat_session_unavailable"
                self.progress("session_unavailable", {"error": str(exc)})
                break
            if not scrolled:
                stop_reason = "scroll_failed"
                break
        if identity_recoveries >= 3:
            stop_reason = "identity_recovery_limit"
        elif new_in_run >= max_new_articles:
            stop_reason = "batch_new_limit"
        elif attempts >= max_open_attempts:
            stop_reason = "batch_open_limit"
        fingerprint = hashlib.sha256("\n".join(sorted(completed_urls)).encode()).hexdigest()
        store.set_checkpoint("mac_human_agent", account.biz, str(len(completed_urls)), fingerprint,
                             False)
        export_all(store, ws.root)
        audit_workspace(store, ws.root)
        ws.write_json("audit/human-agent-progress.json", {
            "account": account_name,
            "declared_article_count": total,
            "captured_article_count": len(completed_urls),
            "attempts": attempts,
            "new_in_run": new_in_run,
            "skipped_known_cards": skipped_known,
            "complete": False,
            "stop_reason": stop_reason,
        })
        final_stage = "incomplete"
        self.progress(final_stage, {
            "workspace": str(ws.root), "captured": len(completed_urls), "total": total,
            "reason": stop_reason, "new_in_run": new_in_run,
            "opens_in_run": attempts, "skipped_known_cards": skipped_known,
        })
        return ws.root
