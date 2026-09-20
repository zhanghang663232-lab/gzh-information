from __future__ import annotations

import csv
import json
from pathlib import Path

from .storage import Store
from .workspace import safe_name


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def export_all(store: Store, root: Path) -> dict[str, int]:
    exports = root / "exports"
    obsidian = root / "obsidian"
    exports.mkdir(parents=True, exist_ok=True)
    obsidian.mkdir(parents=True, exist_ok=True)
    articles = store.rows("SELECT * FROM articles ORDER BY published_at DESC, stable_key")
    metrics = store.rows("SELECT * FROM metric_snapshots ORDER BY captured_at DESC")
    comments = store.rows("SELECT * FROM comments ORDER BY article_key, created_at")
    replies = store.rows("SELECT * FROM comment_replies ORDER BY article_key, created_at")
    missing = store.rows("SELECT * FROM missing_records ORDER BY layer, article_key")
    for name, rows in (("articles", articles), ("metrics", metrics), ("comments", comments),
                       ("comment_replies", replies), ("missing", missing)):
        _write_jsonl(exports / f"{name}.jsonl", rows)
        _write_csv(exports / f"{name}.csv", rows)
    accounts = store.rows("SELECT * FROM accounts LIMIT 1")
    account_name = accounts[0]["name"] if accounts else root.name
    index = [f"# {account_name}", "", f"共发现 {len(articles)} 篇文章。", "", "## 文章索引", ""]
    for article in articles:
        suffix = article["stable_key"].replace(":", "-")[-10:]
        filename = safe_name(article.get("title") or "无标题") + f"-{suffix}.md"
        content = store.rows(
            "SELECT * FROM content_snapshots WHERE article_key=? ORDER BY id DESC LIMIT 1",
            (article["stable_key"],),
        )
        body = ""
        if content and content[0].get("markdown_path"):
            path = root / content[0]["markdown_path"]
            if path.exists():
                body = path.read_text(encoding="utf-8")
        note = (
            f"---\nsource: {article['url']}\nstatus: {article['status']}\n"
            f"published_at: {article.get('published_at','')}\n---\n\n"
            f"# {article.get('title') or '无标题'}\n\n{body}\n"
        )
        (obsidian / filename).write_text(note, encoding="utf-8")
        index.append(f"- [[{filename[:-3]}]]")
    (obsidian / f"{safe_name(account_name)}.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return {"articles": len(articles), "metrics": len(metrics), "comments": len(comments), "replies": len(replies)}
