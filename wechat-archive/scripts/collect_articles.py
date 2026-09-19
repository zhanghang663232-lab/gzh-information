#!/usr/bin/env python3
"""Fetch public WeChat article pages into Markdown packages."""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UA = "Mozilla/5.0 (compatible; PublicArticleArchive/1.0)"
SENSITIVE = {"pass_ticket", "exportkey", "wap_sid2"}

def sanitize_url(url: str) -> str:
    p = urlsplit(url)
    q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k not in SENSITIVE]
    return urlunsplit(("https", p.netloc, p.path, urlencode(q), ""))

def safe_slug(title: str, fallback: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-龥_-]+", "-", title).strip("-")
    return (s or fallback)[:100]

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.author, self.body = [], [], []
        self.in_title = self.in_author = False
        self.body_depth = 0
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); node_id = a.get("id",""); classes = (a.get("class") or "").split()
        if tag == "title": self.in_title = True
        if a.get("name") == "author" or "profile_nickname" in classes: self.in_author = True
        if node_id == "js_content" or "rich_media_content" in classes: self.body_depth = 1
        elif self.body_depth: self.body_depth += 1
        if self.body_depth and tag in {"p","section","div","br","h1","h2","h3","li"}: self.body.append("\n")
    def handle_endtag(self, tag):
        if tag == "title": self.in_title = False
        if self.in_author and tag in {"span","p","div"}: self.in_author = False
        if self.body_depth:
            self.body_depth -= 1
    def handle_data(self, data):
        text = " ".join(data.split())
        if not text: return
        if self.in_title: self.title.append(text)
        if self.in_author: self.author.append(text)
        if self.body_depth: self.body.append(text)

def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def save_package(root, index, url, status, title="", author="", body="", error=""):
    clean = sanitize_url(url); slug = safe_slug(title, f"article-{index:04d}")
    pkg = root / f"{index:04d}-{slug}"; pkg.mkdir(parents=True, exist_ok=True)
    captured = datetime.now(timezone.utc).isoformat()
    metadata = {"source_url": clean, "title": title, "author": author, "status": status, "captured_at": captured}
    report = {"status": status, "source_url": clean, "error": error,
              "sensitive_parameter_found": any(k in url for k in SENSITIVE)}
    fm = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k,v in {
        "title": title or slug, "author": author, "source_url": clean, "captured_at": captured}.items()) + "---\n\n"
    (pkg/"article.md").write_text(fm + f"# {title or slug}\n\n" + (body.strip()+"\n" if body else ""), encoding="utf-8")
    (pkg/"metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (pkg/"extraction-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path); ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--delay", type=float, default=0.3); args = ap.parse_args()
    urls = list(dict.fromkeys(x.strip() for x in args.input.read_text(encoding="utf-8").splitlines() if x.strip()))
    args.output.mkdir(parents=True, exist_ok=True); items = []
    for i, url in enumerate(urls, 1):
        try:
            p = ArticleParser(); p.feed(fetch(url))
            title, author, body = " ".join(p.title).strip(), " ".join(p.author).strip(), "\n".join(p.body)
            status = "ok" if body.strip() else "empty"; save_package(args.output,i,url,status,title,author,body)
            items.append({"index":i,"url":sanitize_url(url),"status":status,"title":title})
        except Exception as exc:
            save_package(args.output,i,url,"failed",error=str(exc))
            items.append({"index":i,"url":sanitize_url(url),"status":"failed","error":str(exc)})
        time.sleep(max(0,args.delay))
    summary = {"total":len(items),"ok":sum(x["status"]=="ok" for x in items),
               "empty":sum(x["status"]=="empty" for x in items),"failed":sum(x["status"]=="failed" for x in items)}
    (args.output/"batch-report.json").write_text(json.dumps({"generated_at":datetime.now(timezone.utc).isoformat(),"summary":summary,"items":items},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False)); return 0 if summary["failed"] == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
