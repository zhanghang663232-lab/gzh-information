from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" .")
    return cleaned[:100] or "未知公众号"


@dataclass(slots=True)
class Workspace:
    root: Path

    @classmethod
    def create(cls, base: Path, account_name: str) -> Workspace:
        ws = cls(base.expanduser().resolve() / safe_name(account_name))
        for name in ("raw", "database", "exports", "obsidian", "audit", "runtime"):
            (ws.root / name).mkdir(parents=True, exist_ok=True)
        return ws

    @classmethod
    def open(cls, root: Path) -> Workspace:
        ws = cls(root.expanduser().resolve())
        if not (ws.root / "database" / "archive.sqlite3").exists():
            raise FileNotFoundError("不是有效的 gzh-information 工作区")
        return ws

    @property
    def database(self) -> Path:
        return self.root / "database" / "archive.sqlite3"

    @property
    def credential(self) -> Path:
        return self.root / "runtime" / "credential.json"

    @property
    def proxy_state(self) -> Path:
        return self.root / "runtime" / "proxy-state.json"

    def write_json(self, relative: str, value: Any, mode: int = 0o644) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, mode)
        temp.replace(target)
        return target

    def scrub_credential(self) -> None:
        if self.credential.exists():
            self.credential.write_text("{}\n", encoding="utf-8")
            os.chmod(self.credential, 0o600)
