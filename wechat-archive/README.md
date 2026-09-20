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

## 从一个链接读取整个账号

新增入口脚本会从一条公开文章或专辑链接中读取 `__biz`，通过导出服务分页获得该账号的**全部可访问公开文章列表**，然后自动执行正文提取、Obsidian 建库与审计。

首次使用需要在导出服务中复制一次 API key 到 macOS 剪贴板；运行时只需要提供链接，key 不会写入命令、日志、仓库或归档目录。

```bash
cd wechat-archive
# 先复制 exporter API key 到剪贴板
python3 scripts/archive_account.py --account-url 'https://mp.weixin.qq.com/s?__biz=...&mid=...'
```

默认输出到 `run/account-<__biz>/`，其中保留可恢复的原始列表页、去重后的 `article-urls.txt`、文章包、Obsidian 文库和 `audit.json`。仅需先建立链接清单时加 `--skip-content`；账号名称无法从列表识别时可加 `--account-name 名称`。

### 真实边界

“整个账号”在这里是指导出服务能够列出的该账号**全部可访问公开文章**。单个公开网页本身没有开放完整历史列表接口，因此没有有效的 exporter API key 时，工具会明确停止，不会猜测、绕过登录或伪造“全量”结果。阅读、点赞、在看、评论、转发等后台/会话指标也不属于此入口。
