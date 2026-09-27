from gzh_reader.human_agent import OcrLine, Window
from gzh_reader.visual_probe import probe_wechat


def test_wechat_visual_probe_distinguishes_visible_blank_window():
    window = Window(4, "微信", 0, 0, 880, 640, 0, 123, True)

    class Controller:
        def activate(self):
            pass

        def windows(self):
            return [window]

        def ocr(self, item):
            assert item == window
            return []

    assert probe_wechat(Controller()) == {
        "window_visible": True, "capture_readable": False,
        "ocr_line_count": 0,
        "sharing_state": None,
        "display_ocr": None,
        "reason": "微信窗口可见，但当前截图没有可识别文字",
    }


def test_wechat_visual_probe_reports_readable_without_disclosing_text():
    window = Window(4, "公众号", 0, 0, 880, 640, 0, 123, True)

    class Controller:
        def activate(self):
            pass

        def windows(self):
            return [window]

        def ocr(self, item):
            return [OcrLine("私人聊天内容", 0, 0, 50, 20)]

    result = probe_wechat(Controller())
    assert result["capture_readable"] is True
    assert "私人聊天内容" not in str(result)


def test_wechat_visual_probe_names_unshared_window():
    window = Window(4, "微信", 0, 0, 880, 640, 0, 123, True, 0)

    class Controller:
        def activate(self):
            pass

        def windows(self):
            return [window]

        def ocr(self, item):
            return []

    result = probe_wechat(Controller())
    assert result["sharing_state"] == 0
    assert "未向系统共享画面" in result["reason"]


def test_wechat_visual_probe_identifies_offscreen_article_window():
    window = Window(4, "微信 (窗口)", 915, 136, 440, 751, 0, 123, False, 1)

    class Controller:
        def activate(self):
            pass

        def windows(self):
            return [window]

        def ocr(self, item):
            raise AssertionError("offscreen window must not be captured")

    result = probe_wechat(Controller())
    assert result["capture_readable"] is False
    assert "不在当前可见桌面" in result["reason"]
