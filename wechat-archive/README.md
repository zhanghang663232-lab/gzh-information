# 微信公众号公开文章归档

可复现、可暂停的公开文章归档流程：公开专辑 HTML → URL 去重 → 批量正文提取 → Obsidian 索引 → 覆盖率/敏感参数审计。

本模块只处理公开专辑和公开文章 URL，不登录、不绕过验证码、不采集后台私有数据，也不获取阅读/点赞/在看/评论/转发等后台指标。不要提交 Cookie、API key、pass_ticket、wap_sid2 或第三方文章正文。

## 使用

先在浏览器中打开公开专辑并保存 HTML，或准备每行一个文章 URL 的 TXT 文件。

```bash
cd wechat-archive
python3 scripts/discover_album_urls.py --html album.html --output run/article-urls.txt
python3 scripts/collect_articles.py --input run/article-urls.txt --output run/articles
python3 scripts/build_obsidian.py --articles run/articles --output run/obsidian --account-name 墙外守候
python3 scripts/audit.py --urls run/article-urls.txt --articles run/articles --output run/audit.json
```

每篇文章生成 `article.md`、`metadata.json`、`extraction-report.json`；批次生成 `batch-report.json`。图片默认只保留外链。输出位于 `run/`，不会进入 Git。

## 技术原则

浏览器只负责发现公开链接；本地 Python 负责批量下载和转换。URL 经过规范化并去重，单篇失败不会中断全批次，报告保留 terminal status。审计失败时不要发布输出。

## 版权和隐私

仓库只保存脚本、配置和文档。文章内容应保存在本地或私有存储，并确认具有相应使用权限。
