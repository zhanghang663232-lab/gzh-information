# gzh-information v2

面向 Apple Silicon Mac 的微信公众号公开数据归档系统。用户粘贴目标公众号任意一篇文章链接，程序会枚举可发现的历史文章，保存正文、采集时互动快照、普通用户可见评论，并输出可审计的数据包。

> 当前为 v2 alpha。原有脚本保留在 `wechat-archive/`，作为 legacy 流程继续存在一个版本周期。v2 是独立 clean-room 实现，没有复制 `Access_wechat_article` 的 CC BY-NC-SA 源码。

## 它是什么

它是 `Access_wechat_article` 思路的 Mac clean-room 重写：只操作用户已经登录的桌面微信，不依赖公众号后台、第三方导出网站、搜索引擎、代理、抓包或本地 CA。

```text
一篇文章链接
   ↓
在桌面微信打开文章并进入公众号主页
   ↓
Vision OCR 识别历史文章卡片，Quartz 模拟点击与滚动
   ↓
从微信内置浏览器复制真实链接与完整渲染文本
   ↓
读取主页/文章底部公开显示的阅读、点赞、转发与评论状态
   ↓
SQLite 合并、续传、覆盖率审计、JSONL/CSV/Markdown/Obsidian 导出
```

“全量”指完整枚举可发现文章，并明确记录所有不可得项。已删除、违规、审核中、公众号后台私有评论等不会伪装为零或成功。互动数字是采集当时的快照，不是历史曲线。

## 双击使用（Mac）

1. 下载仓库或 Release 压缩包。
2. 双击 `安装.command`，首次安装依赖。
3. 双击 `启动.command`，浏览器会打开中文向导。
4. 粘贴文章链接，选择输出目录。
5. 暂时不用电脑时，勾选“允许 Agent 操作桌面微信”并开始。

程序不会读取或保存 Cookie、Credential、API key，也不会修改 HTTP、HTTPS 或 SOCKS 代理。

由于这是“模拟真人操作”的桌面 Agent，运行时微信窗口必须可见，并会被自动点击、滚动和切换。macOS 不允许它在同一桌面上既完成真实 GUI 操作又完全不影响用户前台工作；如果不希望窗口弹出，请先暂停任务，等到电脑空闲时再继续。

## 命令行

```bash
gzh-reader collect --url '<公众号文章链接>' --output '<目录>'
gzh-reader resume --workspace '<已有账号目录>'
gzh-reader audit --workspace '<账号目录>'
gzh-reader export --workspace '<账号目录>' --format all
gzh-reader doctor
gzh-reader proxy restore --state '<proxy-state.json>'
```

命令不需要 API key、代理参数或证书。首次运行需在 macOS 系统设置中允许终端/应用使用“辅助功能”和“屏幕录制”。

## 数据包

```text
<输出目录>/<公众号名>/
  raw/          列表响应、文章 HTML 与规范化 Markdown
  database/     唯一规范化状态库 archive.sqlite3
  exports/      JSONL 与 CSV
  obsidian/     公众号首页、索引和文章正文
  audit/        覆盖率、缺失清单和 SHA-256 清单
  runtime/      可恢复状态；短期凭据在安全关闭后清空
```

状态严格限定为 `pending`、`ok`、`deleted`、`restricted`、`rate_limited`、`credential_expired`、`missing`、`failed`。正文、指标和评论分别记账，一个层失败不会抹掉其他层的成功结果。

## 完整性门槛

- 可访问正文覆盖率目标：≥95%。
- `readNum`、`likeNum`、`oldLikeNum`、`shareNum`、`commentNum` 各自覆盖率目标：≥98%。
- 数字 `0` 原样保存；`100001` 原样标记为平台返回的封顶值，不自行推算。
- Mac Agent 兜底通道只有在出现可验证的列表末尾时才标记完整，否则明确输出 `list_incomplete`。

## 当前边界

- 微信不同版本的界面坐标、OCR 结果和文章卡片布局可能变化；状态机保留失败项并可从断点重试。
- 普通用户看不到的后台私有评论、已删除内容和未公开互动数据无法获取；程序不会伪造。
- 桌面微信必须可见。为了不打扰前台工作，建议在电脑空闲时运行；任务可中断并从 SQLite 断点恢复。
- Release 构建目前生成 Apple Silicon 可双击源码包，尚未进行 Apple Developer 签名/公证。

## 开发

```bash
uv sync --extra dev
uv run pytest -q
```

MIT License。仅用于归档公开可见数据；使用者需遵守平台规则、适用法律和合理速率限制。

