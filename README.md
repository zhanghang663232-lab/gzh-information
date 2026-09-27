# gzh-information v2

面向 Apple Silicon Mac 的微信公众号公开数据归档原型。当前优先验证少量桌面微信界面采集，形成可复用流程；再由确定性程序分批续采，可选用低消耗模型复核难识别的公开列表文字。它尚未完成目标账号全量读取。文章正文和可见互动字段分别记账；缺失字段保持未知。

下一阶段按 [Mac 批量读取执行计划 v3](docs/execution-plan-v3.md) 推进。最终目标是可续传的批量读取；10 篇只是验收门槛。2026-09-27 已在目标账号完成单轮 10 次打开、10 篇新增的桌面微信实机验证；正文批量路径可用，但账号全量列表和全部互动字段尚未达标，详见 [验收记录](docs/acceptance/run-2026-09-27.md)。DeepSeek 和豆包现可共用文章列表 OCR 复核器；基础 Agent 模式可排序当前视口的文章或暂停，完整的工具型 Agent 调度仍待实现。

2026-09-26 曾观察到新版微信主窗口的系统共享状态为 0；后续通过可读取的公众号/文章子窗口继续验证。可运行 `gzh-reader doctor --visual` 复查当前状态。首次仍需人在微信中打开目标公众号主页，不能宣称只贴链接即可从零自动进入账号。

> 当前为 v2 alpha。原有脚本保留在 `wechat-archive/`，作为 legacy 流程继续存在一个版本周期。v2 是独立 clean-room 实现，没有复制 `Access_wechat_article` 的 CC BY-NC-SA 源码。

## 它是什么

它是参考 `Access_wechat_article` 操作思路的 Mac clean-room 实现。当前默认入口操作用户已经登录的桌面微信，不依赖公众号后台、第三方导出网站、代理、抓包或本地 CA。

```text
在微信打开目标文章，进入账号主页；填写链接和账号名称
   ↓
在桌面微信打开文章并进入公众号主页
   ↓
Vision OCR 识别历史文章卡片，Quartz 模拟点击与滚动
   ↓
从微信内置浏览器复制真实链接与完整渲染文本
   ↓
读取主页/文章底部公开显示的互动字段
   ↓
SQLite 合并、续传、覆盖率审计、JSONL/CSV/Markdown/Obsidian 导出
```

“全量”指完整枚举可发现文章，并明确记录所有不可得项。已删除、违规、审核中、公众号后台私有评论等不会伪装为零或成功。互动数字是采集当时的快照，不是历史曲线。

## 双击使用（Mac）

1. 下载仓库或 Release 压缩包，双击 `安装.command`。
2. 在 Mac 微信中打开目标文章，点击公众号名称进入账号主页；核对主页名称。
3. 双击 `启动.command`，在中文向导中填写文章链接、主页名称和输出目录。
4. 保留默认的“本轮最多新增 5 篇”进行试跑；“工作区累计保存上限”可留空。勾选桌面操作确认并开始。
5. 查看工作区 `audit/coverage.json`。试跑确认后可用下方 `resume` 命令继续读取。

默认采集不读取 Cookie、Credential 或 API key，也不会修改 HTTP、HTTPS 或 SOCKS 代理。可选的模型复核会在本次运行中读取用户输入的 API key，但不会持久化。

### 可选：试用 DeepSeek 或豆包

向导里选择供应商，并在“模型 API key”框临时输入密钥，点击“测试模型连接”。测试只发送固定的连接测试文字；密钥不会保存到工作区。勾选模型复核后，只有常规规则无法解析公众号文章卡片时才发送该页公开 OCR 文字，单次运行最多 5 次。文章正文和截图不会发送给模型，模型只建议 OCR 行号；点击坐标、阅读数和点赞数仍由本地程序从原始 OCR 行验证。不开启复核时完全不调用模型。

勾选“Agent 模式”后，所选模型可以对已识别的当前页面文章排序，或要求暂停；本地程序验证编号后才操作微信。此模式每个视口最多调用一次模型，单轮最多 30 次。它尚不具备跨页面的完整工具调度能力。

命令行可运行 `gzh-reader model test --provider deepseek` 或 `gzh-reader model test --provider doubao`；续采时加 `--model-review --review-provider doubao` 可用豆包复核卡片，加 `--agent --review-provider doubao` 可测试受限 Agent 模式。可在终端隐藏输入密钥，也可用 `DEEPSEEK_API_KEY` 或 `ARK_API_KEY` 环境变量；不要把密钥写入仓库或命令参数。默认模型分别为 `deepseek-flash` 和 `doubao-seed-2-1-lite-260915`，可通过 `--model-id` 指定当前账号可用的模型。连接测试会产生一次很小的 API 调用。当前本机 `ARK_API_KEY` 格式无效，因此真实豆包连接尚未验证；不要把“适配器测试通过”当作已连接。

由于这是“模拟真人操作”的桌面 Agent，当前宿主机模式需要微信窗口可见。不打扰宿主机的目标方案是在独立 macOS VM 中运行微信与 Agent；设计与安全边界见 [后台 Agent 路线](docs/background-agent.md)。

## 命令行

```bash
gzh-reader collect --url '<公众号文章链接>' --account-name '<微信主页名称>' --max-new-articles 5 --output '<目录>'
gzh-reader resume --workspace '<已有账号目录>' --max-new-articles 5
gzh-reader resume --workspace '<已有账号目录>'
gzh-reader recover-open --title '<当前文章完整标题>' --account-name '<公众号名>' --url '<起始文章链接>' --output '<目录>'
gzh-reader audit --workspace '<账号目录>'
gzh-reader export --workspace '<账号目录>' --format all
gzh-reader doctor
gzh-reader proxy restore --state '<proxy-state.json>'
```

默认桌面微信命令不需要 API key、代理参数或证书；显式启用模型复核才需要对应供应商密钥。首次运行需在 macOS 系统设置中允许终端/应用使用“辅助功能”和“屏幕录制”。`--max-articles` 是工作区累计保存上限；`--max-new-articles` 是单轮新增上限，默认 5 篇、最高 10 篇，另有最多 3 倍的打开尝试上限。试跑和续采都要求微信客户端保持可见。

`recover-open` 仅用于批次中断后核实并保存当前已打开的单篇文章：不点击列表卡片、不切换标签、不关闭用户窗口。它不证明批量导航已通过，也不会把缺失的点赞、转发字段填成零。

[实机试跑记录与下一阶段门槛](docs/mac-ui-poc.md)

新版微信标签页、小程序弹窗和失败恢复的实际边界也记录在上述试跑文档；这些保护降低误点与重复打开风险，但不保证长批次不会触发微信重新登录。

[批量读取后退出登录的第一性原理排查](docs/logout-investigation.md)

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

状态严格限定为 `pending`、`ok`、`deleted`、`restricted`、`rate_limited`、`credential_expired`、`missing`、`failed`。已落盘的正文、指标和评论分别记账；但当前桌面采集必须先完成文章身份与页尾核对，失败时本篇不会入库，不能把已复制的局部内容算作成功。

## 完整性门槛

- 可访问正文覆盖率目标：≥95%。
- `readNum`、`likeNum`、`oldLikeNum`、`shareNum`、`commentNum` 各自覆盖率目标：≥98%。
- 数字 `0` 原样保存；`100001` 原样标记为平台返回的封顶值，不自行推算。
- 桌面 Agent 只有在出现可验证的列表末尾时才标记完整；主页显示的“原创内容”数量本身不是完整性证明。

## 当前边界

- 微信不同版本的界面坐标、OCR 结果和文章卡片布局可能变化；状态机保留失败项并可从断点重试。
- 每个列表视口和每篇文章都强制校验目标公众号名称；发现串号立即拒绝入库并从样例链接恢复。
- 2026-09-27 实机单轮 10 篇端到端正文小样本通过：10 次打开、10 个不同 URL、10 份通过最低正文文件检查，目标工作区累计 66 份可核查正文；这是可行性证据，不证明每篇全文无遗漏，更不等于 302 篇全量。该 10 篇的阅读、点赞、评论计数均有值，分享数均缺失；`oldLikeNum` 与公开评论明细仍未达到目标。当前本地自动测试为 147 项。详见 [验收记录](docs/acceptance/run-2026-09-27.md)。
- 首次试跑需要人在微信中打开目标账号主页，并填写主页名称。只粘贴链接就自动导航到主页仍是后续工作。
- 长批次曾在微信 OCR/窗口操作阶段停滞；Vision OCR 现有 12 秒子进程硬超时，其他窗口操作的长批次恢复能力仍需实机验证。
- 用户观察到连续读取约 30 篇后微信退出登录；原因尚未证实。程序现在限制每轮新增、打开次数和连续失败，避免重复打开已完成卡片，但不能保证微信不会再次要求登录。
- 普通用户看不到的后台私有评论、已删除内容和未公开互动数据无法获取；程序不会伪造。
- 桌面微信必须可见。为了不打扰前台工作，建议在电脑空闲时运行；任务可中断并从 SQLite 断点恢复。
- Release 构建目前生成 Apple Silicon 可双击源码包，尚未进行 Apple Developer 签名/公证。

## 开发

```bash
uv sync --extra dev
uv run pytest -q
```

MIT License。仅用于归档公开可见数据；使用者需遵守平台规则、适用法律和合理速率限制。
