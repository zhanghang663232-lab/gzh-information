import pytest

from gzh_reader.ui_lock import ui_control_lock


def test_wechat_ui_lock_rejects_second_controller_and_releases(tmp_path):
    path = tmp_path / "ui.lock"
    with ui_control_lock(path):
        with pytest.raises(RuntimeError, match="另一个任务"):
            with ui_control_lock(path):
                pytest.fail("第二个任务不应获得锁")
    with ui_control_lock(path):
        assert path.stat().st_mode & 0o777 == 0o600
