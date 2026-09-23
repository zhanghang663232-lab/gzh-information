from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .storage import Store

SECRET_PATTERNS = {
    "pass_ticket": re.compile(rb"pass_ticket\s*[=:]\s*['\"]?[A-Za-z0-9_%.-]{6,}", re.IGNORECASE),
    "wap_sid2": re.compile(rb"wap_sid2\s*[=:]\s*['\"]?[A-Za-z0-9_%.-]{6,}", re.IGNORECASE),
    "authorization": re.compile(rb"authorization\s*[=:]\s*['\"]?[A-Za-z0-9_.%-]{6,}", re.IGNORECASE),
    "cookie": re.compile(rb"cookie\s*[=:]\s*['\"]?[A-Za-z0-9_.%-]{6,}", re.IGNORECASE),
    "api_key": re.compile(rb"x-auth-key\s*[=:]\s*['\"]?[A-Za-z0-9_.%-]{6,}", re.IGNORECASE),
}


def security_scan(root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    excluded = {"archive.sqlite3", "checksums.sha256"}
    for path in root.rglob("*"):
        if not path.is_file() or path.name in excluded or path.stat().st_size > 5_000_000:
            continue
        data = path.read_bytes()
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                findings.append({"file": str(path.relative_to(root)), "kind": name})
    return findings


def audit_workspace(store: Store, root: Path) -> dict:
    total = store.rows("SELECT COUNT(*) AS n FROM articles")[0]["n"]
    eligible = store.rows(
        "SELECT COUNT(*) AS n FROM articles WHERE status NOT IN ('deleted','restricted')"
    )[0]["n"]
    content_ok = store.rows("SELECT COUNT(DISTINCT article_key) AS n FROM content_snapshots WHERE status='ok'")[0]["n"]
    metric_counts = {}
    for field in ("readNum", "likeNum", "oldLikeNum", "shareNum", "commentNum"):
        metric_counts[field] = store.rows(
            f"SELECT COUNT(DISTINCT article_key) AS n FROM metric_snapshots WHERE {field} IS NOT NULL"
        )[0]["n"]
    list_state = store.rows(
        "SELECT provider,cursor,page_fingerprint,completed,updated_at FROM checkpoints ORDER BY updated_at DESC LIMIT 1"
    )
    report = {
        "definition": "全量=完整枚举可发现文章，并明确记录每个不可得项；互动数据为采集时快照。",
        "articles_discovered": total,
        "list": list_state[0] if list_state else {"completed": 0, "reason": "没有列表检查点"},
        "articles_accessible": eligible,
        "content": {"ok": content_ok, "coverage": (content_ok / eligible if eligible else 0), "gate": 0.95},
        "metrics": {
            field: {"ok": count, "coverage": (count / eligible if eligible else 0), "gate": 0.98}
            for field, count in metric_counts.items()
        },
        "missing": store.rows("SELECT article_key,layer,status,reason,captured_at FROM missing_records ORDER BY layer,article_key"),
        "security_findings": security_scan(root),
    }
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    checksums = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "checksums.sha256"):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}")
    (audit_dir / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return report
