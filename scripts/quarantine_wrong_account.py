from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEPENDENT_TABLES = (
    "content_snapshots",
    "metric_snapshots",
    "comments",
    "comment_replies",
    "assets",
    "missing_records",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quarantine a proven wrong-account capture range without deleting its raw evidence."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--captured-at-or-after", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    database = args.workspace / "database" / "archive.sqlite3"
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in db.execute(
                """SELECT stable_key, url, title, captured_at
                   FROM articles WHERE captured_at >= ? ORDER BY captured_at""",
                (args.captured_at_or_after,),
            )
        ]
    print(json.dumps({"count": len(rows), "articles": rows}, ensure_ascii=False, indent=2))
    if not args.apply or not rows:
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    quarantine = args.workspace / "raw" / "quarantine" / f"wrong-account-{stamp}"
    quarantine.mkdir(parents=True, exist_ok=False)
    backup = args.workspace / "audit" / f"archive-before-wrong-account-quarantine-{stamp}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)

    moved: list[str] = []
    for row in rows:
        file_key = row["stable_key"].replace(":", "_")
        for relative in (
            Path("raw/human-agent") / f"{file_key}.json",
            Path("raw/markdown") / f"{file_key}.md",
        ):
            source = args.workspace / relative
            if not source.exists():
                continue
            target = quarantine / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, target)
            moved.append(str(relative))

    keys = [row["stable_key"] for row in rows]
    placeholders = ",".join("?" for _ in keys)
    with sqlite3.connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        for table in DEPENDENT_TABLES:
            db.execute(f"DELETE FROM {table} WHERE article_key IN ({placeholders})", keys)
        db.execute(f"DELETE FROM articles WHERE stable_key IN ({placeholders})", keys)
        db.commit()

    manifest = {
        "reason": "wrong_account_profile_detected",
        "captured_at_or_after": args.captured_at_or_after,
        "quarantined_at": datetime.now(UTC).isoformat(),
        "database_backup": str(backup.relative_to(args.workspace)),
        "moved_files": moved,
        "articles": rows,
    }
    (quarantine / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"quarantine": str(quarantine), "backup": str(backup)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
