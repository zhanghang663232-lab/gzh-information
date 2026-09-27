#!/usr/bin/env python3
"""Build a reproducible, allowlisted Apple Silicon source distribution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile
import tomllib
import zipfile


REQUIRED_FILES = (
    "README.md", "LICENSE", "pyproject.toml", "uv.lock", ".gitignore",
    "安装.command", "启动.command",
    "docs/mac-delivery.md", "docs/agent-start.md", "docs/build-history.md",
    "docs/troubleshooting.md", "docs/background-agent.md",
    "docs/batch-agent-plan.md", "docs/execution-plan-v3.md",
    "docs/feasibility-gate.md", "docs/first-principles-review.md",
    "docs/logout-investigation.md", "docs/mac-ui-poc.md",
    "docs/acceptance/run-2026-09-26.md",
    "docs/acceptance/run-2026-09-27.md",
    "scripts/package_mac.py",
)
FORBIDDEN_PARTS = {
    ".git", ".venv", "__pycache__", "runtime", "raw", "database", "exports",
    "obsidian", "audit", "gzh-information-data", "dist", "build",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(rb"(?:pass_ticket|wap_sid2)[=\"'\s:]+[A-Za-z0-9%+/=_-]{24,}", re.I),
    re.compile(rb"(?:cookie|authorization)[\"'\s]*[:=][\"'\s]*(?:Bearer\s+)?[A-Za-z0-9_-]{32,}", re.I),
)


def _allowed_source(path: Path) -> bool:
    return (
        path.suffix == ".py"
        and not any(part.lower() in FORBIDDEN_PARTS for part in path.parts)
        and not any(word in path.name.lower() for word in ("credential", "cookie", "proxy-state"))
        and not path.name.startswith(".")
    )


def source_files(root: Path) -> list[Path]:
    root = root.resolve()
    paths = [root / name for name in REQUIRED_FILES]
    paths.extend(
        path for path in (root / "src" / "gzh_reader").rglob("*.py")
        if _allowed_source(path.relative_to(root))
    )
    if not any(path.relative_to(root).as_posix() == "src/gzh_reader/__init__.py" for path in paths):
        raise ValueError("缺少 src/gzh_reader 包")
    for path in paths:
        if not path.is_file():
            raise ValueError(f"缺少交付文件：{path.relative_to(root)}")
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"禁止打包符号链接或仓库外文件：{path.relative_to(root)}")
    return sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())


def build_package(root: Path, output_dir: Path | None = None) -> Path:
    root = root.resolve()
    output_dir = (output_dir or root / "dist").resolve()
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    if not re.fullmatch(r"[A-Za-z0-9.+_-]+", version):
        raise ValueError("版本号不能包含路径字符")
    name = f"gzh-information-{version}-macos-arm64-source"
    payloads = {}
    for path in source_files(root):
        content = path.read_bytes()
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise ValueError(f"疑似凭据；已停止打包，请检查：{path.relative_to(root)}")
        payloads[path.relative_to(root).as_posix()] = content
    manifest = {
        "package": name,
        "kind": "source-preview",
        "platform": "macOS Apple Silicon",
        "python": "3.12",
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in payloads.items()},
        "exclusions": "Runtime data, article archives, credentials, virtual environments and Git history are not included.",
    }
    payloads["PACKAGE-MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{name}.zip"
    with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".zip", delete=False) as temporary:
        temp_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, content in sorted(payloads.items()):
                entry = zipfile.ZipInfo(f"{name}/{relative}", date_time=(2020, 1, 1, 0, 0, 0))
                entry.create_system = 3
                executable = relative.endswith(".command") or relative == "scripts/package_mac.py"
                entry.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, content)
        temp_path.replace(destination)
    finally:
        temp_path.unlink(missing_ok=True)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".zip.sha256").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="默认输出到项目 dist/（已被 Git 忽略）")
    args = parser.parse_args()
    try:
        destination = build_package(args.root, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"无法打包：{exc}\n")
    print(destination)
    print(destination.with_suffix(".zip.sha256"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
