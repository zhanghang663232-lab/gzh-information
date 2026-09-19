#!/usr/bin/env python3
"""Audit coverage, missing package files and credential-like strings."""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
SENSITIVE = re.compile(r"(pass_ticket|exportkey|wap_sid2|Cookie:|Authorization:)", re.I)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", required=True, type=Path); ap.add_argument("--articles", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path); args = ap.parse_args()
    urls = list(dict.fromkeys(x.strip() for x in args.urls.read_text(encoding="utf-8").splitlines() if x.strip()))
    notes = sorted(args.articles.glob("*/article.md"))
    missing = [str(p.parent) for p in notes if not (p.parent/"metadata.json").exists()]
    hits = []
    for p in args.articles.rglob("*"):
        if p.is_file() and p.stat().st_size < 20_000_000 and SENSITIVE.search(p.read_text(encoding="utf-8",errors="ignore")): hits.append(str(p))
    result = {"unique_input_urls":len(urls),"article_notes":len(notes),"missing_metadata":missing,
              "sensitive_parameter_files":hits,"content_coverage":len(notes)/len(urls) if urls else 0.0,
              "safe_to_publish":not missing and not hits}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False)); return 0 if result["safe_to_publish"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
