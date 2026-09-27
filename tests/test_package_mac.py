import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import zipfile

import pytest


spec = importlib.util.spec_from_file_location(
    "package_mac", Path(__file__).parents[1] / "scripts" / "package_mac.py"
)
package_mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_mac)


def fixture_repo(root):
    for relative in package_mac.REQUIRED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sample\n")
    (root / "pyproject.toml").write_text('[project]\nversion = "2.0.0a1"\n')
    source = root / "src" / "gzh_reader"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    return source


def test_package_excludes_runtime_and_has_reproducible_checksums_and_permissions(tmp_path):
    source = fixture_repo(tmp_path)
    for relative in (".env", ".git/config", ".venv/secret", "raw/article.html", "runtime/cookie.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private data")
    (source / "cookie.py").write_text("private data")
    (source / "runtime").mkdir()
    (source / "runtime" / "state.py").write_text("private data")
    (tmp_path / "docs" / "doubao-handoff.md").write_text("personal notes")
    archive = package_mac.build_package(tmp_path)
    first = archive.read_bytes()
    assert package_mac.build_package(tmp_path).read_bytes() == first
    with zipfile.ZipFile(archive) as bundle:
        prefix = bundle.namelist()[0].split("/")[0] + "/"
        names = {name.removeprefix(prefix) for name in bundle.namelist()}
        assert names == set(package_mac.REQUIRED_FILES) | {"src/gzh_reader/__init__.py", "PACKAGE-MANIFEST.json"}
        assert "docs/acceptance/run-2026-09-27.md" in names
        assert "AGENTS.md" in names
        assert "docs/README.md" in names
        assert "docs/doubao-handoff.md" not in names
        manifest = json.loads(bundle.read(prefix + "PACKAGE-MANIFEST.json"))
        for path, digest in manifest["files"].items():
            assert hashlib.sha256(bundle.read(prefix + path)).hexdigest() == digest
        for script in ("安装.command", "启动.command"):
            assert stat.S_IMODE(bundle.getinfo(prefix + script).external_attr >> 16) == 0o755
    assert archive.with_suffix(".zip.sha256").read_text().startswith(hashlib.sha256(first).hexdigest())


def test_package_rejects_secret_in_source_before_creating_archive(tmp_path):
    source = fixture_repo(tmp_path)
    (source / "example.py").write_text('api_key = "sk-' + "x" * 30 + '"\n')
    with pytest.raises(ValueError, match="疑似凭据"):
        package_mac.build_package(tmp_path)
    assert not (tmp_path / "dist").exists()


def test_package_rejects_source_symlink(tmp_path):
    source = fixture_repo(tmp_path)
    (source / "alias.py").symlink_to(source / "__init__.py")
    with pytest.raises(ValueError, match="符号链接"):
        package_mac.build_package(tmp_path)
