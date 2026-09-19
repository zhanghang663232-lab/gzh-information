#!/usr/bin/env python3
"""Create an Obsidian entry note and wikilinked article index."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", required=True, type=Path); ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--account-name", required=True); args = ap.parse_args()
    packages = sorted(args.articles.glob("*/article.md"))
    account = args.output / args.account_name; article_dir = account / "文章"; article_dir.mkdir(parents=True, exist_ok=True)
    for source in packages:
        dest = article_dir / source.parent.name / "article.md"; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    lines = ["---",f"title: {args.account_name}｜文章索引","tags:","  - 微信公众号/文章索引",f"account_name: {args.account_name}",f"article_count: {len(packages)}","---","",f"# {args.account_name}｜文章索引","","> [!note] 说明","> 以下链接指向每篇文章的 Obsidian 正文笔记。","",
             "| 序号 | 标题 |","| ---: | --- |"]
    for n, source in enumerate(packages,1):
        title = source.parent.name; meta = source.parent/"metadata.json"
        if meta.exists(): title = json.loads(meta.read_text(encoding="utf-8")).get("title") or title
        title = title.replace("|","｜").replace("[","〔").replace("]","〕")
        lines.append(f"| {n} | [[{args.account_name}/文章/{source.parent.name}/article|{title}]] |")
    (account/"文章索引.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    entry = ["---",f"title: {args.account_name}","tags:","  - 微信公众号","  - 对标账号",f"account_name: {args.account_name}",f"public_article_count: {len(packages)}","---","",f"# {args.account_name}","",f"> [!info] 归档概况\n> 已整理 **{len(packages)} 篇**公开文章。","", "## 使用入口","",f"- [[{args.account_name}/文章索引|文章索引]]：浏览全部文章。","", "## 数据边界","", "本目录只代表公开文章归档，不等同于公众号后台私有历史消息库。"]
    (args.output/f"{args.account_name}.md").write_text("\n".join(entry)+"\n", encoding="utf-8")
    print(f"articles={len(packages)} output={args.output}"); return 0

if __name__ == "__main__":
    raise SystemExit(main())
