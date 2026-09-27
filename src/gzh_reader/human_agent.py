from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

from .audit import audit_workspace
from .candidate_queue import prioritize_candidates
from .content_quality import meaningful_body, verified_body_file
from .exports import export_all
from .models import Account, ArticleSeed, ContentSnapshot, MetricSnapshot, Status, utc_now
from .storage import Store
from .urls import article_stable_key, normalize_url
from .ui_lock import ui_control_lock
from .workspace import Workspace

if TYPE_CHECKING:
    from .agent_runtime import CardPlanAgent
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
    onscreen: bool = False
    sharing_state: int | None = None


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


class ArticleNotOpenedError(RuntimeError):
    """The card click did not produce a verified article view."""


COUNT_RE = re.compile(r"([\d.]+)\s*([万萬wW]?)")
METRIC_RE = re.compile(r"阅读\s*([\d.]+\s*[万萬wW]?)\s*赞\s*([\d.]+\s*[万萬wW]?)")


def _plain_title(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def titles_conflict(first: str, second: str) -> bool:
    left, right = _plain_title(first), _plain_title(second)
    if len(left) < 8 or len(right) < 8:
        return left != right
    return not (
        left[:8] in right or right[:8] in left
        or SequenceMatcher(None, left, right).ratio() >= 0.78
    )


def visible_article_title_line(title: str, lines: list[OcrLine]) -> OcrLine | None:
    """Accept OCR punctuation drift, but not an unrelated article title."""
    expected = _plain_title(title)
    if len(expected) < 6:
        return None
    for line in lines:
        if not 55 <= line.cy <= 290:
            continue
        observed = _plain_title(line.text)
        if len(observed) < 6:
            continue
        if expected in observed or observed in expected and len(observed) >= 8:
            return line
        if SequenceMatcher(None, expected, observed).ratio() >= 0.78:
            return line
    return None


def article_title_visible(title: str, lines: list[OcrLine]) -> bool:
    return visible_article_title_line(title, lines) is not None


def target_article_tabs(title: str, lines: list[OcrLine], window_width: float) -> list[OcrLine]:
    """Find observed title labels, without inferring controls beside them."""
    expected = _plain_title(title)
    if len(expected) < 8:
        return []
    return [
        line for line in lines
        if line.cy < 52 and 75 < line.cx < window_width - 95
        and _plain_title(line.text).startswith(expected[:6])
    ]


def target_article_tab(title: str, lines: list[OcrLine], window_width: float) -> OcrLine | None:
    """Return a tab only when its title label is unique."""
    matches = target_article_tabs(title, lines, window_width)
    return matches[0] if len(matches) == 1 else None


def menu_matches_article_tab(
    title: str, lines: list[OcrLine], window_width: float, menu_x: float,
) -> bool:
    """Reject a menu icon outside the observed target article's tab."""
    tabs = target_article_tabs(title, lines, window_width)
    if not tabs:
        return False
    target = max(tabs, key=lambda line: line.x)
    # Tab widths vary with window size, but the active ellipsis is inside its
    # own tab, to the right of the visible title and before another tab label.
    if not target.x + 45 <= menu_x <= min(target.x + 345, window_width - 12):
        return False
    # The WeChat 4.x AI control contains its own three dots. Do not let a
    # visually detected glyph inside that observed control become a click.
    if any(
        line.cy < 52 and line.x >= window_width - 150
        and "向AI" in line.text
        and line.x - 8 <= menu_x <= line.x + line.width + 8
        for line in lines
    ):
        return False
    later_labels = [
        line.x for line in lines
        if line.cy < 52 and line.x > target.x + 95
        and len(line.text.strip()) >= 4
        # WeChat 4.x OCR can read the adjacent "向AI" toolbar control as
        # "••向AI". It is not another article tab.
        and not (line.x >= window_width - 150 and "向AI" in line.text)
    ]
    return not later_labels or menu_x < min(later_labels)


def tab_menu_x(lines: list[OcrLine], window_width: float) -> float | None:
    """Estimate the last tab's menu from observed tab spacing, never window width alone."""
    labels = sorted(
        (line for line in lines
         if line.cy < 52 and 75 < line.x < window_width - 115
         and len(line.text.strip()) >= 2),
        key=lambda line: line.x,
    )
    starts: list[float] = []
    for label in labels:
        if not starts or label.x - starts[-1] >= 85:
            starts.append(label.x)
    if len(starts) < 2:
        return None
    spacing = starts[-1] - starts[-2]
    if not 110 <= spacing <= 350:
        return None
    candidate = starts[-1] + spacing * 0.77
    return candidate if candidate < window_width - 85 else None


def article_menu_x(title: str, lines: list[OcrLine], window_width: float) -> float | None:
    """Locate its ellipsis from the observed tab pitch, not OCR text width."""
    tab = target_article_tab(title, lines, window_width)
    if tab is None:
        return None
    previous = sorted(
        (line for line in lines
         if line.cy < 52 and 75 < line.x < tab.x - 110
         and len(line.text.strip()) >= 2),
        key=lambda line: line.x,
    )
    if not previous:
        return None
    pitch = tab.x - previous[-1].x
    if not 120 <= pitch <= 320:
        return None
    candidate = tab.x + pitch * 0.79
    return candidate if 75 < candidate < window_width - 85 else None


def account_tab_line(account_name: str, lines: list[OcrLine]) -> OcrLine | None:
    """The close/reload glyph can be joined to the account tab by Vision OCR."""
    matches = [
        line for line in lines
        if line.cy < 52 and line.text.strip().endswith(account_name)
        and len(line.text.strip()) <= len(account_name) + 3
    ]
    return matches[0] if len(matches) == 1 else None


def copy_link_menu_line(lines: list[OcrLine]) -> OcrLine | None:
    items = [line for line in lines if line.text.strip() == "复制链接"]
    anchors = ("刷新", "调整文字大小", "全文翻译", "用默认浏览器打开", "关闭全部标签页")
    matched = sum(any(anchor in line.text for line in lines) for anchor in anchors)
    return items[0] if len(items) == 1 and matched >= 2 else None


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
        # Keep OCR word boundaries: "赞159 1个朋友转发" contains a second
        # number that must not be glued onto the like count as 1591.
        match = METRIC_RE.search(line.text)
        if not match:
            continue
        candidates: list[OcrLine] = []
        for previous in reversed(ordered[:index]):
            if previous.cy <= last_metric_y + 4 or line.cy - previous.cy > 130:
                break
            if (
                previous.cy < 180
                and previous.text.strip().startswith("全部")
                and "文" in previous.text
            ):
                continue
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
        # WeChat 4.1.x keeps a sticky account/navigation header over the
        # first partially clipped card. Its metric row may still be OCR'd,
        # but clicking it does not open an article.
        click_y = candidates[-1].cy
        if click_y < 150:
            last_metric_y = line.cy
            continue
        if window_height is not None and click_y > window_height - 75:
            last_metric_y = line.cy
            continue
        cards.append(ProfileCard(
            title=title,
            read_num=parse_count(match.group(1)),
            like_num=parse_count(match.group(2)),
            click_x=candidates[-1].cx,
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


def completed_human_rows(store: Store, account_name: str, root: Path) -> list[dict]:
    """Resume across the legacy `human-` and current `human:` account IDs."""
    rows = store.rows(
        """SELECT a.url, a.title, m.readNum, m.likeNum,
                  c.markdown_path, c.status AS content_status FROM articles a
           LEFT JOIN metric_snapshots m ON m.id = (
             SELECT MAX(m2.id) FROM metric_snapshots m2
             WHERE m2.article_key=a.stable_key AND m2.readNum IS NOT NULL)
           JOIN content_snapshots c ON c.id = (
             SELECT MAX(c2.id) FROM content_snapshots c2
             WHERE c2.article_key=a.stable_key)
           WHERE a.biz IN (
             SELECT biz FROM accounts
             WHERE name=? AND source='mac_human_agent'
           )""",
        (account_name,),
    )
    return [
        row for row in rows
        if row["content_status"] == Status.OK.value
        and verified_body_file(root, row["markdown_path"])
    ]


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


def verified_account_name(lines: list[OcrLine], requested: str | None) -> str:
    """Honor an exact user-supplied identity after verifying the profile UI."""
    if requested is not None:
        if not profile_matches_account(lines, requested):
            raise AccountMismatchError(
                f"公众号主页不匹配：期望 {requested}，看到 {account_name_from_profile(lines)}"
            )
        return requested
    return account_name_from_profile(lines)


def profile_matches_account(lines: list[OcrLine], account_name: str) -> bool:
    texts = {line.text.strip() for line in lines}
    account_at_top = any(
        account_name in line.text and len(line.text.strip()) <= len(account_name) + 5
        and line.cy < 150 for line in lines
    )
    if not account_at_top:
        return False
    if any("篇原创内容" in text for text in texts):
        return True
    # The account header collapses while its article list is scrolled. Keep
    # recognizing that same tab using its fixed top navigation, not the
    # author signature at the bottom of an article.
    nav = "".join(line.text.strip() for line in lines if line.cy < 180)
    return "全部" in nav and "文" in nav and any(
        "阅读" in text for text in texts
    )


def profile_identity_conflicts(lines: list[OcrLine], account_name: str) -> bool:
    """Only judge identity when the profile header is actually visible."""
    return any("篇原创内容" in line.text for line in lines) and not profile_matches_account(
        lines, account_name
    )


class AccountMismatchError(RuntimeError):
    """A clicked article belongs to another account; discard the viewport."""


class LinkTitleConflictError(RuntimeError):
    """A captured URL is already verified under a different article title."""


class MiniProgramInterceptedError(RuntimeError):
    """A mini-program prompt means the card click did not open the intended article."""


class WechatLoginRequiredError(RuntimeError):
    """WeChat explicitly asked the user to sign in again."""


def is_login_required_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "为了你的账号安全" in compact and "重新登录" in compact


def article_matches_account(bottom_text: str, account_name: str) -> bool:
    return account_name in {line.strip() for line in bottom_text.splitlines()}


class MacHumanController:
    def __init__(self) -> None:
        if subprocess.run(
            ["uname", "-s"], capture_output=True, text=True, check=False
        ).stdout.strip() != "Darwin":
            raise RuntimeError("Mac 微信人工模拟器只能在 macOS 运行")
        import AppKit
        import ApplicationServices
        import Quartz

        self.AppKit = AppKit
        self.AX = ApplicationServices
        self.Quartz = Quartz
        self.tabbed_profile_account: str | None = None

    def activate(self) -> None:
        apps = self.AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            "com.tencent.xinWeChat"
        )
        if not apps:
            subprocess.run(
                ["open", "-b", "com.tencent.xinWeChat"],
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
        # Stage Manager can move a live WeChat window out of the current
        # visible group when the local task/terminal becomes frontmost.
        # Enumerate all windows, then raise and verify the exact target before
        # OCR or input instead of treating an off-screen window as logged out.
        rows = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionAll, q.kCGNullWindowID)
        result: list[Window] = []
        for row in rows:
            owner = str(row.get(q.kCGWindowOwnerName, ""))
            # WeChat 4.1.15 can host detached public-account/article windows
            # in WeChatAppEx rather than in the main WeChat process. Titles
            # and account OCR are still checked before any interaction.
            if owner not in {"微信", "微信 2", "WeChat", "WeChatAppEx"}:
                continue
            bounds = row.get(q.kCGWindowBounds, {})
            result.append(Window(
                number=int(row.get(q.kCGWindowNumber, 0)),
                title=str(row.get(q.kCGWindowName, "")),
                x=float(bounds.get("X", 0)), y=float(bounds.get("Y", 0)),
                width=float(bounds.get("Width", 0)), height=float(bounds.get("Height", 0)),
                layer=int(row.get(q.kCGWindowLayer, 0)),
                pid=int(row.get(q.kCGWindowOwnerPID, 0)),
                onscreen=bool(row.get(q.kCGWindowIsOnscreen, False)),
                sharing_state=row.get(getattr(q, "kCGWindowSharingState", "kCGWindowSharingState")),
            ))
        return result

    def window(self, title: str, *, min_width: float = 200) -> Window | None:
        matches = [item for item in self.windows() if item.title == title and item.width >= min_width]
        return min(matches, key=lambda item: (not item.onscreen, item.layer, -item.width)) if matches else None

    def browser_windows(self) -> list[Window]:
        """Return readable WeChat browser windows without guessing which is active."""
        return [item for item in self.windows()
                if item.title == "微信 (窗口)" and item.width >= 300
                and item.onscreen and item.sharing_state != 0]

    def browser_by_number(self, number: int) -> Window | None:
        return next((item for item in self.browser_windows() if item.number == number), None)

    def login_required(self) -> bool:
        # The security notice appears in a small, untitled WeChat window.
        # Detect it without clicking or accepting anything in the dialog.
        if not hasattr(getattr(self, "Quartz", None), "CGWindowListCopyWindowInfo"):
            return False
        dialogs = [item for item in self.windows() if item.onscreen and not item.title
                   and 250 <= item.width <= 900 and 150 <= item.height <= 800]
        for dialog in dialogs:
            try:
                if is_login_required_text("\n".join(line.text for line in self.ocr(dialog))):
                    return True
            except RuntimeError:
                continue
        return False

    def require_active_session(self) -> None:
        if self.login_required():
            raise WechatLoginRequiredError("微信明确要求重新登录；采集已停止，等待用户处理")

    def wait_window(self, title: str, timeout: float = 8) -> Window:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.window(title)
            if found:
                return found
            time.sleep(0.2)
        raise RuntimeError(f"没有找到微信窗口：{title}")

    def require_foreground(self, window: Window) -> None:
        """Check ownership without activating anything or dismissing a menu."""
        current = next((item for item in self.windows() if item.number == window.number), None)
        front = self.AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        allowed = {window.pid} | {
            int(app.processIdentifier()) for app in
            self.AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                "com.tencent.xinWeChat")
        }
        if (current is None or not current.onscreen or front is None
                or int(front.processIdentifier()) not in allowed
                or (current.x, current.y, current.width, current.height)
                != (window.x, window.y, window.width, window.height)):
            raise RuntimeError("微信目标窗口焦点或位置已改变，已停止点击")

    def ocr(self, window: Window, *, top_fraction: float = 1.0,
            visible: bool = False,
            region: tuple[float, float, float, float] | None = None) -> list[OcrLine]:
        # Vision occasionally blocks indefinitely inside performRequests_error_.
        # Keep it in a short-lived child process so a stuck OCR call cannot
        # strand the whole collection run or prevent its checkpoint/export.
        if region is not None:
            if not visible:
                raise ValueError("屏幕局部 OCR 必须使用合成画面")
            left, top, width, height = region
            if (left < 0 or top < 0 or width <= 0 or height <= 0
                    or left + width > window.width or top + height > window.height):
                raise ValueError("OCR 区域超出目标窗口")
        else:
            left, top, width, height = 0, 0, window.width, window.height
        args = [sys.executable, "-m", "gzh_reader.ocr_worker",
                str(window.number), str(width), str(height), str(top_fraction)]
        if visible:
            self.require_foreground(window)
            args.extend(["--visible", str(window.x + left), str(window.y + top)])
        result = None
        for attempt in range(2 if visible else 1):
            try:
                result = subprocess.run(
                    args, capture_output=True, text=True, check=False,
                    timeout=8 if visible else 12,
                    env={**os.environ, "GZH_OCR_TRACE": "1"},
                )
                break
            except subprocess.TimeoutExpired as exc:
                if visible and attempt == 0:
                    self.require_foreground(window)
                    continue
                trace = (exc.stderr or b"")
                if isinstance(trace, bytes):
                    trace = trace.decode("utf-8", errors="replace")
                phase = trace.strip().splitlines()[-1:] or ["unknown"]
                raise RuntimeError(
                    f"微信窗口 OCR 超时（阶段 {phase[0]}）；本篇跳过并保留断点"
                ) from exc
        assert result is not None
        if result.returncode != 0:
            raise RuntimeError(
                "微信窗口 OCR 失败：" + (result.stderr.strip()[-300:] or str(result.returncode))
            )
        try:
            payload = json.loads(result.stdout)
            lines = [OcrLine(**item) for item in payload["lines"]]
            if region is not None:
                for line in lines:
                    line.x += left
                    line.y += top
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("微信窗口 OCR 返回了无效结果") from exc
        return sorted(lines, key=lambda item: (item.y, item.x))

    def article_menu_center(self, window: Window) -> tuple[float, float]:
        # Keep Quartz image objects out of the long-lived controller. A base
        # window screenshot in this process made the next child display
        # capture stall; two isolated captures do not share that state.
        args = [sys.executable, "-m", "gzh_reader.ocr_worker", "--menu-center",
                str(window.number), str(window.width), str(window.height)]
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    check=False, timeout=8)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("文章菜单图标截图超时，已停止") from exc
        if result.returncode != 0:
            raise RuntimeError("文章菜单图标截图失败：" + result.stderr.strip()[-200:])
        try:
            centers = json.loads(result.stdout)["centers"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("文章菜单图标识别结果无效") from exc
        if len(centers) != 1:
            raise RuntimeError("未找到唯一的文章圆形菜单按钮，已停止")
        return tuple(centers[0])

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
        running_apps = list(
            self.AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                "com.tencent.xinWeChat"
            )
        )
        pids = [target.pid]
        for running in running_apps:
            pid = int(running.processIdentifier())
            if pid not in pids:
                pids.append(pid)
        for pid in pids:
            app = ax.AXUIElementCreateApplication(pid)
            error, windows = ax.AXUIElementCopyAttributeValue(app, ax.kAXWindowsAttribute, None)
            if error != 0 or not windows:
                continue
            matches = []
            for item in windows:
                _, title = ax.AXUIElementCopyAttributeValue(item, ax.kAXTitleAttribute, None)
                if str(title or "") == target.title:
                    matches.append(item)
            # A title alone cannot identify one of multiple same-named
            # windows. Do not send clicks or Command-W into an ambiguous UI.
            if len(matches) != 1:
                continue
            for running in running_apps:
                if int(running.processIdentifier()) == pid:
                    running.activateWithOptions_(
                        self.AppKit.NSApplicationActivateAllWindows
                        | self.AppKit.NSApplicationActivateIgnoringOtherApps
                    )
                    break
            if ax.AXUIElementPerformAction(matches[0], ax.kAXRaiseAction) == 0:
                time.sleep(0.25)
                return True
        # WeChat 4.1.x may expose its WebView window to Quartz while AX omits
        # it. Fall back only when the exact window is the sole visible match
        # after activating its owning process; never infer focus from a title
        # that could belong to another tab/window.
        owner = self.AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
            target.pid
        )
        if owner is None:
            return False
        owner.activateWithOptions_(
            self.AppKit.NSApplicationActivateAllWindows
            | self.AppKit.NSApplicationActivateIgnoringOtherApps
        )
        time.sleep(0.25)
        visible = [
            item for item in self.windows()
            if item.onscreen and item.title == target.title
        ]
        return len(visible) == 1 and visible[0].number == target.number

    def focus_profile(self) -> Window:
        self.require_active_session()
        account_name = getattr(self, "tabbed_profile_account", None)
        if account_name:
            profile = self._tabbed_profile(account_name)
            if profile is None:
                raise RuntimeError("新版微信标签页未能恢复目标公众号主页，已停止")
            return profile
        if self.window("公众号") is None:
            self.activate()
        for viewer in [item for item in self.windows() if item.title == "图片和视频"]:
            self._close_window(viewer)
            time.sleep(0.2)
        profile = self.wait_window("公众号")
        self._raise(profile)
        return self.wait_window("公众号")

    def focus_browser(self) -> Window:
        self.require_active_session()
        if getattr(self, "tabbed_profile_account", None):
            browser = self.wait_window("微信 (窗口)")
            if not self._raise(browser):
                raise RuntimeError("无法前置新版微信文章标签，已停止")
            lines = self.ocr(browser, top_fraction=0.45)
            if profile_matches_account(lines, self.tabbed_profile_account):
                raise RuntimeError("当前仍是公众号主页，未确认文章标签已打开")
            if len("".join(item.text for item in lines)) < 50:
                raise RuntimeError("文章标签内容尚未加载，已停止")
            return browser
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

    def _tabbed_profile(self, account_name: str) -> Window | None:
        browsers = self.browser_windows()
        if not browsers:
            self.profile_probe_reason = "browser_offscreen_or_missing"
            return None
        observed: list[tuple[Window, list[OcrLine]]] = []
        for browser in browsers:
            try:
                observed.append((browser, self.ocr(browser)))
            except RuntimeError:
                # A failed capture is not evidence that this is the target.
                continue
        profiles = [(browser, lines) for browser, lines in observed
                    if profile_matches_account(lines, account_name)]
        if len(profiles) > 1:
            self.profile_probe_reason = "multiple_matching_profiles"
            return None
        if len(profiles) == 1:
            browser = profiles[0][0]
            if not self._raise(browser):
                self.profile_probe_reason = "profile_window_not_raised"
                return None
            browser = self.browser_by_number(browser.number)
            if browser is None or not profile_matches_account(self.ocr(browser), account_name):
                self.profile_probe_reason = "profile_changed_after_raise"
                return None
            self.profile_probe_reason = "matched"
            return browser
        # A visible WebView can be absent from the AX tree. Only an observed,
        # unique account tab is a safe route to the profile.
        tabs = [(browser, tab) for browser, lines in observed
                if (tab := account_tab_line(account_name, lines)) is not None]
        if len(tabs) != 1:
            self.profile_probe_reason = (
                "multiple_account_tabs" if tabs else "account_tab_not_identified"
            )
            return None
        browser, tab = tabs[0]
        if not self._raise(browser):
            self.profile_probe_reason = "account_tab_window_not_raised"
            return None
        self.require_foreground(browser)
        self.click(browser.x + tab.cx, browser.y + tab.cy)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            browser = self.browser_by_number(browser.number)
            if browser is None:
                return None
            if profile_matches_account(self.ocr(browser), account_name):
                self.profile_probe_reason = "matched_after_tab_click"
                return browser
            time.sleep(0.3)
        return None

    def wait_article(self, title: str, timeout: float = 8) -> Window:
        if not getattr(self, "tabbed_profile_account", None):
            return self.wait_window("微信 (窗口)", timeout=timeout)
        deadline = time.monotonic() + timeout
        last_state = "window_missing"
        tabs: list[str] = []
        switched_to_target_tab = False
        scrolled_target_to_top = False
        ocr_retry_after_scroll = False
        ocr_timeout_retried = False
        unconfirmed_observations = 0
        while time.monotonic() < deadline:
            browser = self.window("微信 (窗口)")
            if browser is not None:
                try:
                    lines = self.ocr(browser, top_fraction=0.3)
                except RuntimeError as exc:
                    if "OCR 超时" in str(exc):
                        if scrolled_target_to_top and not ocr_retry_after_scroll:
                            ocr_retry_after_scroll = True
                            continue
                        if not ocr_timeout_retried:
                            # Base-window Quartz capture can stall just after
                            # a new tab opens. Use the composed foreground
                            # display once, without another click or tab
                            # switch, before treating the page as unavailable.
                            ocr_timeout_retried = True
                            try:
                                lines = self.ocr(
                                    browser, visible=True,
                                    region=(0, 0, browser.width, min(260, browser.height)),
                                )
                            except RuntimeError as fallback_exc:
                                raise RuntimeError(
                                    f"窗口截图及前台截图均失败：{exc}；{fallback_exc}"
                                ) from fallback_exc
                        else:
                            raise
                    else:
                        raise
                text = "".join(line.text for line in lines)
                tabs = [line.text[:35] for line in lines if line.cy < 52][-5:]
                if profile_matches_account(lines, self.tabbed_profile_account):
                    last_state = "profile_still_active"
                elif len(text) < 80:
                    last_state = "blank_or_unloaded_article"
                else:
                    last_state = "different_article_or_ocr_mismatch"
                if (
                    last_state != "profile_still_active"
                    and article_title_visible(title, lines)
                ):
                    return browser
                unconfirmed_observations += 1
                if unconfirmed_observations % 3 == 0:
                    # The permission dialog is centered below the cropped
                    # article header, so inspect the whole window periodically.
                    if any("即将打开小程序" in line.text for line in self.ocr(browser)):
                        self.dismiss_miniprogram_prompt()
                        raise MiniProgramInterceptedError(
                            "误点触发小程序弹窗，已取消并停止本轮"
                        )
                matching_tabs = target_article_tabs(title, lines, browser.width)
                # A slow page can create multiple same-title tabs. Selecting
                # the rightmost *observed title label* is safe; later actions
                # still require independent page-title and account checks.
                tab = max(matching_tabs, key=lambda line: line.cx) if matching_tabs else None
                if (
                    not switched_to_target_tab
                    and tab is not None
                ):
                    # WeChat 4.1.x can create the target as an inactive tab,
                    # leaving either the profile or a different article active.
                    # Select that exact tab; OCR may include toolbar icons, so
                    # counting Ctrl-Tab steps is not reliable.
                    self.click(browser.x + tab.cx, browser.y + tab.cy)
                    switched_to_target_tab = True
                    time.sleep(0.4)
                    continue
                if (
                    switched_to_target_tab and not scrolled_target_to_top
                    and tab is not None and last_state != "profile_still_active"
                ):
                    self.require_foreground(browser)
                    self.hotkey(126, self.Quartz.kCGEventFlagMaskCommand)
                    scrolled_target_to_top = True
                    time.sleep(0.3)
                    continue
            time.sleep(0.3)
        raise ArticleNotOpenedError(
            f"点击后未确认目标文章已打开（{last_state}；顶部标签：{' / '.join(tabs)}）；"
            "已停止本轮，避免重复点击"
        )

    def confirm_current_article(self, title: str) -> Window:
        """Verify an already-visible article without selecting or opening tabs."""
        browser = self.window("微信 (窗口)")
        if browser is None:
            raise ArticleNotOpenedError("当前没有微信文章窗口")
        self.require_foreground(browser)
        self.hotkey(126, self.Quartz.kCGEventFlagMaskCommand)
        for _ in range(2):
            current = self.window("微信 (窗口)")
            if current is None or current.number != browser.number:
                raise ArticleNotOpenedError("文章窗口在核对期间发生变化")
            try:
                lines = self.ocr(current, top_fraction=0.3)
            except RuntimeError as exc:
                if "OCR 超时" in str(exc):
                    continue
                raise
            if article_title_visible(title, lines):
                return current
        raise ArticleNotOpenedError("当前页面未能确认目标文章标题；未选择其他标签")

    def open_profile(self, url: str, account_name: str | None = None) -> Window:
        """Restore the account profile from a public article in WeChat itself."""
        self.require_active_session()
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

        if account_name:
            tabbed = self._tabbed_profile(account_name)
            if tabbed is not None:
                self.tabbed_profile_account = account_name
                return tabbed
            browsers = self.browser_windows()
            if browsers:
                articles: list[Window] = []
                for candidate in browsers:
                    try:
                        lines = self.ocr(candidate)
                    except RuntimeError:
                        continue
                    if article_matches_account(
                        "\n".join(line.text for line in lines), account_name
                    ):
                        articles.append(candidate)
                if len(articles) != 1:
                    raise RuntimeError(
                        "可见微信窗口中无法唯一确认目标公众号文章；已停止，未关闭或点击其他窗口"
                        f"（候选 {len(articles)} 个；{getattr(self, 'profile_probe_reason', 'unknown')}）"
                    )
                article = articles[0]
                if not self._raise(article):
                    raise RuntimeError("无法前置唯一的目标文章窗口；已停止")
                self.require_foreground(article)
                # The account footer is usually below a long article. Move
                # only the verified article to its bottom, then inspect the
                # same exact Quartz window before clicking its avatar.
                self.hotkey(125, self.Quartz.kCGEventFlagMaskCommand)
                time.sleep(0.6)
                article = self.browser_by_number(article.number)
                if article is None:
                    raise RuntimeError("滚动后目标文章窗口不可见；已停止")
                footer_links = [
                    line for line in self.ocr(article)
                    if line.text.strip() == account_name
                    and line.cy > article.height * 0.7
                ]
                if len(footer_links) != 1:
                    raise RuntimeError("文章页尾未能唯一确认目标公众号入口；已停止，未点击头像")
                self.require_foreground(article)
                name_line = footer_links[0]
                avatar_x = name_line.x - max(22.0, name_line.height * 1.4)
                self.click(article.x + avatar_x, article.y + name_line.cy)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    tabbed = self._tabbed_profile(account_name)
                    if tabbed is not None:
                        self.tabbed_profile_account = account_name
                        return tabbed
                    time.sleep(0.2)
                raise RuntimeError("已点击经核对的公众号头像，但未能确认主页；已停止")
            if any(item.title == "微信 (窗口)" and not item.onscreen
                   for item in self.windows()):
                raise RuntimeError(
                    "微信文章窗口存在但不在当前可见桌面，无法截图；已停止且未盲点。"
                    "请手动在微信打开一篇目标公众号文章，确认文章窗口可见后重试"
                )
            if any(item.title == "微信 (窗口)" and item.onscreen
                   and item.sharing_state == 0 for item in self.windows()):
                raise RuntimeError("微信文章窗口未向系统共享画面；已停止且未尝试截图或点击")
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
            # WeChat 4.1.15 can show a public-account profile as a tab in this
            # same window. Closing it here would discard the exact profile the
            # user prepared, so stop until the tabbed flow has its own state
            # transitions and close-tab verification.
            if account_name and profile_matches_account(self.ocr(browser), account_name):
                raise RuntimeError(
                    "目标公众号主页位于新版微信标签页；当前采集器尚未适配标签页，已停止且未关闭窗口"
                )
            if account_name:
                raise RuntimeError(
                    "微信中存在标签页，但未确认目标公众号主页；请先切换到目标主页，已停止且未关闭窗口"
                )
            self._close_window(browser)
            time.sleep(0.8)
        main = self.wait_window("微信")
        self._raise(main)
        if main.sharing_state == 0:
            raise RuntimeError(
                "当前微信主窗口未向系统共享画面，程序无法辨认聊天或搜索结果。"
                "请先在微信打开目标文章，再用图形向导检查文章窗口能否读取；"
                "此状态下已停止自动点击"
            )
        marker = url.rstrip("/").rsplit("/", 1)[-1]
        main_lines = self.ocr(main)
        if not main_lines:
            # An onscreen Quartz window is not evidence that WeChat has
            # rendered its signed-in UI. This can happen during relogin or
            # when the window is still blank after a version update.
            raise RuntimeError(
                "微信主窗口当前为空白或无法识别；已停止，未点击任何聊天。"
                "请确认微信聊天列表已实际显示，再打开目标公众号主页重试"
            )
        links = [
            line for line in main_lines
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

    def dismiss_miniprogram_prompt(self) -> bool:
        browser = self.window("微信 (窗口)")
        if browser is None:
            return False
        lines = self.ocr(browser)
        if not any("即将打开小程序" in line.text for line in lines):
            return False
        cancel = [line for line in lines if line.text.strip() == "取消"]
        if len(cancel) != 1:
            raise RuntimeError("微信小程序弹窗出现，但无法唯一确认取消按钮；已停止")
        self.click(browser.x + cancel[0].cx, browser.y + cancel[0].cy)
        time.sleep(0.2)
        if any("即将打开小程序" in line.text for line in self.ocr(browser)):
            raise RuntimeError("微信小程序弹窗未关闭；已停止，请手动点击取消")
        return True

    def copy_page_text(self, title: str) -> str:
        browser = self.focus_browser()
        if self.dismiss_miniprogram_prompt():
            raise MiniProgramInterceptedError("小程序弹窗已取消，拒绝继续复制文章")
        # WeChat 4.1.x may focus only the title widget when it is clicked:
        # Cmd-A/C then copies nothing. Verify the title, but focus a visible
        # paragraph of the article itself before selecting the page text.
        lines = self.ocr(browser, top_fraction=0.72)
        title_line = visible_article_title_line(title, lines)
        if title_line is None:
            raise RuntimeError("未确认文章标题可见，拒绝复制可能属于其他页面的正文")
        body_lines = [
            line for line in lines
            if line.cy > title_line.cy + 60
            and line.cy < browser.height * 0.72
            and len(line.text.strip()) >= 18
            and line.text.strip() != title_line.text.strip()
        ]
        if not body_lines:
            raise RuntimeError("文章首屏没有可确认的正文段落，拒绝盲点复制")
        self.click(browser.x + body_lines[0].cx, browser.y + body_lines[0].cy)
        time.sleep(0.15)
        if self.dismiss_miniprogram_prompt():
            raise MiniProgramInterceptedError("标题点击触发小程序弹窗，拒绝继续复制文章")
        self.AppKit.NSPasteboard.generalPasteboard().clearContents()
        self.hotkey(0, self.Quartz.kCGEventFlagMaskCommand)
        self.hotkey(8, self.Quartz.kCGEventFlagMaskCommand)
        time.sleep(0.4)
        body = self.clipboard()
        if not meaningful_body(body):
            raise RuntimeError("微信未复制到有效正文；已拒绝把链接或空白标为成功")
        if _plain_title(title)[:8] not in _plain_title(body):
            raise RuntimeError("复制结果不含目标文章标题；已拒绝入库")
        return body

    def copy_link(self, title: str | None = None) -> str:
        browser = self.window("微信 (窗口)")
        if browser is None:
            raise RuntimeError("文章窗口已消失，拒绝操作菜单")
        self.require_foreground(browser)
        if self.dismiss_miniprogram_prompt():
            raise MiniProgramInterceptedError("小程序弹窗已取消，拒绝继续复制链接")
        header_lines = self.ocr(browser, top_fraction=0.4)
        if title and not article_title_visible(title, header_lines):
            raise RuntimeError("当前文章标题未确认，拒绝打开链接菜单")
        menu_x, menu_y = self.article_menu_center(browser)
        if title and not menu_matches_article_tab(
            title, header_lines, browser.width, menu_x
        ):
            raise RuntimeError("文章菜单按钮不属于目标文章标签；拒绝复制可能属于其他文章的链接")
        region = (
            max(0, menu_x - 240), 38,
            min(280, browser.width - max(0, menu_x - 240)),
            min(580, browser.height - 38),
        )
        line = copy_link_menu_line(self.ocr(browser, visible=True, region=region))
        if line is None:
            self.require_foreground(browser)
            self.click(browser.x + menu_x, browser.y + menu_y)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and line is None:
                # Raising the app here dismisses the menu. Read the composed
                # screen without changing focus, and require its other items.
                line = copy_link_menu_line(
                    self.ocr(browser, visible=True, region=region)
                )
                if line is None:
                    time.sleep(0.15)
        if line is None:
            visible_text = "".join(
                item.text for item in self.ocr(browser, visible=True)
            )
            if "微信小微" in visible_text and "协议" in visible_text:
                raise RuntimeError(
                    "误触微信『向AI』服务协议；已停止，未同意或拒绝。"
                    "请手动关闭弹窗后检查文章菜单位置"
                )
            raise RuntimeError("文章菜单未显示复制链接，已停止")
        self.require_foreground(browser)
        self.AppKit.NSPasteboard.generalPasteboard().clearContents()
        self.click(browser.x + line.cx, browser.y + line.cy)
        for _ in range(20):
            value = self.clipboard().strip()
            if value.startswith("https://mp.weixin.qq.com/"):
                return normalize_url(value)
            time.sleep(0.1)
        raise RuntimeError("文章菜单未能复制真实链接")

    def page_bottom(self) -> str:
        browser = self.focus_browser()
        self.hotkey(125, self.Quartz.kCGEventFlagMaskCommand)
        time.sleep(0.6)
        current = self.window("微信 (窗口)")
        if current is None or current.number != browser.number:
            raise RuntimeError("读取页尾时文章窗口已改变；拒绝使用其他页面的指标")
        # A short article's footer may contain fewer than 50 OCR characters.
        # Do not apply focus_browser's top-of-article text threshold here.
        return "\n".join(line.text for line in self.ocr(current))

    def close_article(self) -> None:
        self.require_active_session()
        browser = self.window("微信 (窗口)")
        if browser is None:
            return
        account_name = getattr(self, "tabbed_profile_account", None)
        if account_name and profile_matches_account(self.ocr(browser), account_name):
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
        args = [sys.executable, "-m", "gzh_reader.ocr_worker", "--scrollbar-thumb",
                str(profile.number), str(profile.width), str(profile.height)]
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    check=False, timeout=8)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("滚动条截图超时，已停止") from exc
        if result.returncode != 0:
            raise RuntimeError("滚动条识别失败：" + result.stderr.strip()[-200:])
        try:
            value = json.loads(result.stdout)["thumb"]
            if value is None:
                return None
            start, end = map(float, value)
            if not 0 <= start < end <= profile.height:
                raise ValueError("thumb outside window")
            return start, end
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("滚动条识别结果无效") from exc

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
        agent: CardPlanAgent | None = None,
    ):
        self.progress = progress
        self.controller = controller or MacHumanController()
        self.reviewer = reviewer
        self.agent = agent
        self.action: Callable[[str], None] = lambda stage: None

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
        self, profile: Window, card: ProfileCard, expected_account: str,
        *, known_url: str | None = None,
        on_link: Callable[[str], None] | None = None,
        already_open: bool = False,
        close_after: bool = True,
    ) -> HumanCapture:
        phase = "before_click_card"
        article_confirmed = False
        if not already_open:
            self.action("before_click_card")
            self.controller.click(profile.x + card.click_x, profile.y + card.click_y)
        try:
            phase = "wait_article"
            if already_open and hasattr(self.controller, "confirm_current_article"):
                self.controller.confirm_current_article(card.title)
            else:
                wait_article = getattr(self.controller, "wait_article", None)
                if wait_article is not None:
                    wait_article(card.title, timeout=30)
                else:
                    self.controller.wait_window("微信 (窗口)", timeout=7)
            article_confirmed = True
            self.action("article_window_open")
            time.sleep(0.5)
            phase = "copy_link"
            if known_url:
                if not known_url.startswith("https://mp.weixin.qq.com/"):
                    raise RuntimeError("已保存的文章链接不是微信文章 URL")
                url = normalize_url(known_url)
                self.action("verified_saved_link")
            else:
                self.action("before_copy_link")
                url = self.controller.copy_link(card.title)
                if on_link is not None:
                    on_link(url)
            phase = "copy_body"
            self.action("before_copy_body")
            body = self.controller.copy_page_text(card.title)
            phase = "page_bottom"
            self.action("before_page_bottom")
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
            footer_read = parse_count(read_match.group(1)) if read_match else None
            # Cross-check the footer read count against the profile-card value:
            # the two are snapshots seconds apart and must agree in magnitude.
            # A spurious OCR "万/w" can inflate the footer value 10,000x (e.g.
            # 2706 -> 27060000); fall back to the card count in that case.
            if footer_read is not None and card.read_num:
                hi = max(footer_read, card.read_num)
                lo = max(1, min(footer_read, card.read_num))
                if hi / lo >= 50:
                    footer_read = card.read_num
            return HumanCapture(
                url=url, title=card.title, body=body,
                read_num=footer_read if footer_read is not None else card.read_num,
                like_num=card.like_num,
                share_num=parse_share_num(bottom), comment_num=comment_num,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            exc.capture_step = phase
            raise
        finally:
            if article_confirmed and close_after:
                self.action("before_close_article")
                primary_error = sys.exc_info()[1]
                try:
                    self.controller.close_article()
                    self.action("article_window_closed")
                except RuntimeError:
                    self.action("article_cleanup_error")
                    if primary_error is None:
                        raise
            elif not article_confirmed:
                self.action("article_unconfirmed_left_open")
            if close_after:
                try:
                    self.controller.focus_profile()
                except RuntimeError:
                    # A WeChat child window occasionally closes its parent
                    # profile as well; the caller restores it from the seed.
                    pass

    def _save(self, ws: Workspace, store: Store, account: Account, capture: HumanCapture) -> bool:
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
        required_metrics = {
            "readNum": capture.read_num,
            "likeNum": capture.like_num,
            "shareNum": capture.share_num,
            "commentNum": capture.comment_num,
        }
        absent = [name for name, value in required_metrics.items() if value is None]
        metric_status = Status.MISSING if absent else Status.OK
        store.save_metrics(MetricSnapshot(
            article_key=key, readNum=capture.read_num, likeNum=capture.like_num,
            shareNum=capture.share_num, commentNum=capture.comment_num,
            source="mac_human_agent",
            checksum=hashlib.sha256(json.dumps({
                "readNum": capture.read_num, "likeNum": capture.like_num,
                "shareNum": capture.share_num, "commentNum": capture.comment_num,
            }, sort_keys=True).encode()).hexdigest(),
            status=metric_status,
            reason="" if metric_status == Status.OK else "未能从可见界面识别：" + "、".join(absent),
        ), audit_fields=required_metrics)
        store.save_comments(
            key, [], Status.OK if capture.comment_num == 0 else Status.MISSING,
            "" if capture.comment_num == 0 else "桌面页面只能确认公开评论计数，评论明细尚未展开",
        )
        return body_ok

    def recover_open_article(
        self, *, title: str, account_name: str, seed_url: str, output: Path,
    ) -> tuple[Path, bool]:
        """Checkpoint one already-open article after a bounded batch failure.

        This is a recovery path, not proof that profile discovery or batch
        navigation works. It never clicks a card or closes the user's tab.
        """
        with ui_control_lock():
            self.controller.tabbed_profile_account = account_name
            browser = self.controller.window("微信 (窗口)")
            if browser is None:
                raise RuntimeError("当前没有可见的微信文章窗口")
            card = ProfileCard(title, None, None, 0, 0)
            capture = self._capture_card(
                browser, card, account_name, already_open=True,
                close_after=False,
            )
            ws = Workspace.create(output, account_name)
            store = Store(ws.database)
            account = Account(
                biz="human:" + hashlib.sha256(account_name.encode()).hexdigest()[:20],
                name=account_name, source_url=seed_url,
                source="mac_human_agent", status=Status.OK,
            )
            store.upsert_account(account)
            saved = self._save(ws, store, account, capture)
            if saved:
                error_path = ws.root / "audit" / "human-agent-last-error.json"
                try:
                    receipt = json.loads(error_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    receipt = {}
                fingerprint = receipt.get("viewport_fingerprint")
                index = receipt.get("card_index")
                card_count = receipt.get("card_count")
                if (receipt.get("account") == account_name
                        and receipt.get("title") == title
                        and isinstance(fingerprint, str)
                        and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                        and type(index) is int
                        and type(card_count) is int
                        and 0 <= index < card_count):
                    hints_path = ws.root / "audit" / "human-agent-card-hints.json"
                    try:
                        prior = json.loads(hints_path.read_text(encoding="utf-8"))
                        hints = prior.get("cards", {}) if prior.get("biz") == account.biz else {}
                        if not isinstance(hints, dict):
                            hints = {}
                    except (OSError, ValueError, TypeError, AttributeError):
                        hints = {}
                    hints[f"{fingerprint}|{index}"] = capture.url
                    ws.write_json("audit/human-agent-card-hints.json", {
                        "biz": account.biz, "cards": hints,
                    })
            export_all(store, ws.root)
            audit_workspace(store, ws.root)
            return ws.root, saved

    def collect(
        self, url: str, output: Path, *, max_articles: int | None = None,
        account_name: str | None = None, max_new_articles: int = 5,
    ) -> Path:
        with ui_control_lock():
            return self._collect_unlocked(
                url, output, max_articles=max_articles,
                account_name=account_name, max_new_articles=max_new_articles,
            )

    def _collect_unlocked(
        self, url: str, output: Path, *, max_articles: int | None = None,
        account_name: str | None = None, max_new_articles: int = 5,
    ) -> Path:
        if not 1 <= max_new_articles <= 10:
            raise ValueError("本轮最多新增篇数必须在 1 到 10 之间")
        self.controller.open_profile(url, account_name)
        profile = self.controller.scroll_to_top()
        lines = self.controller.ocr(profile)
        account_name = verified_account_name(lines, account_name)
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
        completed_rows = completed_human_rows(store, account_name, ws.root)
        completed_urls = {row["url"] for row in completed_rows}
        completed_titles = {row["url"]: row["title"] for row in completed_rows}
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
        action_trace: list[dict] = []

        def record_action(stage: str) -> None:
            action_trace.append({
                "at": utc_now(), "stage": stage,
                "opens_in_run": attempts, "new_in_run": new_in_run,
            })
            del action_trace[:-40]
            ws.write_json("audit/human-agent-action-trace.json", {
                "account": account_name, "events": action_trace,
            })
            self.progress("human_action", {
                "action": stage, "opens_in_run": attempts,
                "new_in_run": new_in_run,
            })

        self.action = record_action
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
            if not cards:
                # The virtualized list can paint a frame after the account
                # header. Re-observe once without clicking or scrolling;
                # never send a wheel event into an unverified page or modal.
                self.progress("profile_cards_missing", {
                    "ocr_lines": len(lines),
                    "profile_verified": profile_matches_account(lines, account_name),
                    "probe_reason": getattr(self.controller, "profile_probe_reason", ""),
                })
                try:
                    retry_lines = self.controller.ocr(profile)
                except RuntimeError as exc:
                    stop_reason = "ocr_unavailable"
                    self.progress("human_retry", {"error": f"{type(exc).__name__}: {exc}"})
                    break
                if not profile_matches_account(retry_lines, account_name):
                    stop_reason = "profile_lost_before_scroll"
                    self.progress("session_unavailable", {
                        "error": "公众号列表未能二次核对；未滚动或点击",
                        "ocr_lines": len(retry_lines),
                    })
                    break
                lines = retry_lines
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
            if repeated == 0:
                self.progress("human_viewport", {
                    "fingerprint": fingerprint[:12], "cards": len(cards),
                    "new_in_run": new_in_run,
                })
            restart_profile = False
            card_counts = Counter(card_signature(card) for card in cards)
            card_order = list(range(len(cards)))
            if self.agent is not None and cards:
                try:
                    decided_order = self.agent.plan_cards(cards)
                except RuntimeError as exc:
                    stop_reason = "agent_decision_failed"
                    self.progress("agent_decision_failed", {"error": str(exc)[:240]})
                    break
                if decided_order is None:
                    stop_reason = "agent_paused"
                    break
                card_order = decided_order
                self.progress("agent_plan", {"cards": len(cards), "calls": self.agent.calls})
            queue = prioritize_candidates(cards, completed_rows, preferred_order=card_order)
            card_order = list(queue.order)
            if queue.deferred_duplicate:
                self.progress("candidate_deferred", {
                    "count": len(queue.deferred_duplicate),
                    "reason": "标题已出现，保留候选并延后真实链接核对",
                })
            # Same-title cards must only be re-opened for real URL verification
            # *after* scrolling the whole list and confirming there is no unseen
            # article below. Reading counts drift over time, so a saved article's
            # (title, read, like) signature no longer matches and the card cannot
            # be skipped by signature. When every clickable card in this viewport
            # is a same-title duplicate, click nothing and fall through to the
            # scroll() at the loop tail to load fresh cards.
            clickable_order = [
                idx for idx in card_order if idx not in queue.deferred_duplicate
            ]
            if not clickable_order and card_order:
                self.progress("candidate_scroll_for_new", {
                    "reason": "当前视口均为已见标题，向下滚动寻找未采集文章",
                })
                card_order = []
            for index in card_order:
                card = cards[index]
                if attempts >= max_open_attempts or new_in_run >= max_new_articles:
                    break
                hint = f"{fingerprint}|{index}"
                if hint in seen_hints:
                    continue
                seen_hints.add(hint)
                signature = card_signature(card)
                # Viewport fingerprints and card indexes are navigation hints,
                # not stable identity across runs. An old hint may point at a
                # different card after WeChat reflows the list; always copy a
                # fresh URL for an opened card unless a completed article's
                # exact signature can be skipped without opening it.
                known_url = known_cards.get(signature) if card_counts[signature] == 1 else None
                if known_url in completed_urls:
                    if titles_conflict(card.title, completed_titles[known_url]):
                        stop_reason = "link_title_conflict"
                        self.progress("link_title_conflict", {
                            "candidate_title": card.title,
                            "stored_title": completed_titles[known_url],
                            "reason": "旧卡片位置映射到另一标题的链接；拒绝沿用",
                        })
                        halt = True
                        break
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
                    def save_link_receipt(link: str) -> None:
                        card_hints[hint] = link
                        ws.write_json("audit/human-agent-card-hints.json", {
                            "biz": account.biz, "cards": card_hints,
                        })

                    capture = self._capture_card(
                        profile, card, account_name,
                        known_url=known_url,
                        on_link=save_link_receipt,
                    )
                    if capture.url in completed_titles and titles_conflict(
                        capture.title, completed_titles[capture.url]
                    ):
                        raise LinkTitleConflictError(
                            f"候选标题《{capture.title}》与已存链接标题《{completed_titles[capture.url]}》不一致"
                        )
                    card_hints[hint] = capture.url
                    ws.write_json("audit/human-agent-card-hints.json", {
                        "biz": account.biz, "cards": card_hints,
                    })
                    if capture.url not in completed_urls:
                        body_ok = self._save(ws, store, account, capture)
                        if body_ok:
                            completed_urls.add(capture.url)
                            completed_titles[capture.url] = capture.title
                            completed_rows.append({
                                "title": capture.title,
                                "url": capture.url,
                                "readNum": capture.read_num,
                                "likeNum": capture.like_num,
                            })
                            new_in_run += 1
                            self.progress("human_saved", {
                                "new_in_run": new_in_run,
                                "captured_article_count": len(completed_urls),
                            })
                            if known_cards.get(signature) not in (None, capture.url):
                                known_cards.pop(signature, None)
                                ambiguous_cards.add(signature)
                            elif signature not in ambiguous_cards:
                                known_cards[signature] = capture.url
                        else:
                            consecutive_failures += 1
                            self.progress("body_missing", {
                                "title": card.title,
                                "reason": "文章正文复制失败；记录已保留但不计为完成",
                            })
                            stop_reason = "body_missing"
                            halt = True
                    if capture.url in completed_urls:
                        consecutive_failures = 0
                        identity_recoveries = 0
                except LinkTitleConflictError as exc:
                    stop_reason = "link_title_conflict"
                    self.progress("link_title_conflict", {"error": str(exc)})
                    halt = True
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
                    stop_reason = "account_mismatch"
                    halt = True
                    break
                except (RuntimeError, OSError, ValueError) as exc:
                    consecutive_failures += 1
                    failure_step = getattr(exc, "capture_step", "unknown")
                    record_action("article_error")
                    ws.write_json("audit/human-agent-last-error.json", {
                        "at": utc_now(), "account": account_name,
                        "title": card.title, "attempt": attempts,
                        "viewport_fingerprint": fingerprint,
                        "card_index": index,
                        "card_count": len(cards),
                        "step": failure_step, "error_type": type(exc).__name__,
                        "error": str(exc)[:240],
                    })
                    self.progress("human_retry", {
                        "title": card.title,
                        "step": failure_step,
                        "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                    })
                    if isinstance(exc, WechatLoginRequiredError):
                        stop_reason = "wechat_login_required"
                        halt = True
                        break
                    if isinstance(exc, ArticleNotOpenedError):
                        stop_reason = "article_not_opened"
                    elif failure_step == "copy_link":
                        stop_reason = "link_copy_failed"
                    else:
                        stop_reason = f"{failure_step}_failed"
                    halt = True
                    # _capture_card owns article cleanup in its finally block.
                    # A second close here can close the restored profile (or
                    # the next tab) after a body/link failure.
                    try:
                        profile = self.controller.focus_profile()
                        if not profile_matches_account(
                            self.controller.ocr(profile), account_name
                        ):
                            raise RuntimeError("返回的页面不是目标公众号列表")
                    except RuntimeError as restore_error:
                        stop_reason = "profile_restore_failed"
                        self.progress("session_unavailable", {
                            "error": str(restore_error),
                        })
                        halt = True
                    restart_profile = True
                ws.write_json("audit/human-agent-progress.json", {
                    "account": account_name,
                    "declared_article_count": total,
                    "captured_article_count": len(completed_urls),
                    "attempts": attempts,
                    "new_in_run": new_in_run,
                    "skipped_known_cards": skipped_known,
                    "complete": False,
                })
                if halt or restart_profile or new_in_run >= max_new_articles or attempts >= max_open_attempts or (
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
                # Opening and closing an article can reflow or scroll the list.
                # Remaining cards still have coordinates from the old screenshot.
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
                record_action("before_scroll")
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
        login_required = getattr(self.controller, "login_required", None)
        if login_required is not None:
            try:
                if login_required():
                    stop_reason = "wechat_login_required"
            except RuntimeError:
                pass
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
