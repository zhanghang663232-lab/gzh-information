# gzh-information v2

面向 Apple Silicon Mac 的微信公众号公开数据归档系统。用户粘贴目标公众号任意一篇文章链接，程序会枚举可发现的历史文章，保存正文、采集时互动快照、普通用户可见评论，并输出可审计的数据包。

> 当前为 v2 alpha。原有脚本保留在 `wechat-archive/`，作为 legacy 流程继续存在一个版本周期。v2 是独立 clean-room 实现，没有复制 `Access_wechat_article` 的 CC BY-NC-SA 源码。

## 它是什么

它不是让 Agent 盲目逐篇点击，而是把人工动作压缩成一次真实微信会话触发：

```text
一篇文章链接
   ↓
导出服务枚举完整文章列表（保存分页证据）
   ↓
公开网页批量归档正文
   ↓
用户在 Mac 微信打开一篇新文章，短暂捕获该公众号 Credential
   ↓
确定性程序批量采集五项指标与公开可见评论
   ↓
SQLite 合并、续传、覆盖率审计、JSONL/CSV/Markdown/Obsidian 导出
```

“全量”指完整枚举可发现文章，并明确记录所有不可得项。已删除、违规、审核中、公众号后台私有评论等不会伪装为零或成功。互动数字是采集当时的快照，不是历史曲线。

## 双击使用（Mac）

1. 下载仓库或 Release 压缩包。
2. 双击 `安装.command`，首次安装依赖。
3. 双击 `启动.command`，浏览器会打开中文向导。
4. 粘贴文章链接和导出服务 API key，选择输出目录。
5. 若采集互动数据，先阅读并勾选代理确认；按提示在桌面微信打开一篇尚未使用过的目标文章。

API key 只存在于当前进程内存。Cookie/Credential 写入权限为 `0600`，只能匹配当前公众号，并在任务结束后清空。程序保存并分别恢复原有 HTTP、HTTPS、SOCKS 代理。

首次使用 mitmproxy 仍需用户按其本地页面说明安装并信任本地 CA。系统不会绕过证书确认，也不会轮换账号、设备或 IP。

## 命令行

```bash
gzh-reader collect --url '<公众号文章链接>' --output '<目录>'
gzh-reader resume --workspace '<已有账号目录>'
gzh-reader audit --workspace '<账号目录>'
gzh-reader export --workspace '<账号目录>' --format all
gzh-reader doctor
gzh-reader proxy restore --state '<proxy-state.json>'
```

采集互动数据需同时加 `--engagement --allow-system-proxy`。不建议把 API key 放到 `--api-key`，省略该参数会使用隐藏输入，避免进入 shell 历史。

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

- 默认列表通道依赖 mptext 兼容导出 API；API key 有时效。
- 微信 Credential 按公众号隔离且有效期很短，因此指标/评论必须在同一个前台任务中完成；过期后提示再次打开新文章。
- Mac Accessibility/Vision 状态机已提供安全接口与完整性判定，但 alpha 版优先使用“人工打开一次 + MITM”的稳定路径。不同微信版本的全自动导航仍需实机适配，绝不在不确定时声称列表完整。
- Release 构建目前生成 Apple Silicon 可双击源码包，尚未进行 Apple Developer 签名/公证。

## 开发

```bash
uv sync --extra dev
uv run pytest -q
```

MIT License。仅用于归档公开可见数据；使用者需遵守平台规则、适用法律和合理速率限制。

