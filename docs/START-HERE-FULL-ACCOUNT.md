# 豆包从这里开始：整账号读取交接（2026-09-28）

**目标**：用已登录的 Mac 微信桌面界面，逐批读取用户指定公众号的公开历史文章、正文及可见互动，并让 SQLite、导出文件和审计清单可以续传。不要改走第三方网站、后台接口、代理或抓包。模型（豆包/DeepSeek）可以协调与复核，但不能凭模型猜按钮、文章 URL 或缺失指标。

**当前真实状态**：2026-09-28 新一轮 Netskao 工作区已有 **5 篇**通过最低正文长度检查；主页显示约 1272 篇原创内容，但列表 `complete=false`。`readNum`、`likeNum`、`commentNum` 为 5/5，`shareNum`、`oldLikeNum` 为 0/5；评论明细未验收。5 篇不证明全量，也不证明正文逐字完整。此前 1 篇属于较早的另一份工作区，不要用它覆盖最新数据。

默认产出只在本机 `~/Downloads/obsidian/公众号读取/<公众号名>/`。已有 Netskao 数据已复制到该目录，原工作区保留作备份。后续只从这个目录续传；不把正文、SQLite、截图或凭据上传 GitHub。

请按 [完整运行手册](doubao-full-account-runbook.md) 的门 1→门 2→门 3 执行。先在本地同一工作区单篇复测标题 OCR、标签主页恢复和隐藏菜单；真实 URL、正文首尾、账号一致且微信仍登录后，运行**不带篇数上限**的 `resume`，让一个进程持续读取，不用每 10 篇手动重启。不要设置 `--max-articles` 或 `--max-new-articles`。任何一步无法唯一核对时停并读 `audit/human-agent-last-error.json`、`human-agent-action-trace.json`、`human-agent-progress.json`。

遇到故障先查 [历次故障总账](incident-ledger.md) 与 [按停止原因排查](troubleshooting.md)。修代码要加脱敏复现测试，运行 `.venv/bin/python -m pytest -q`，不上传运行数据。最终只有列表末尾证据、去重文章及正文覆盖率、五项指标各自覆盖率、评论明细状态和缺失清单都可审计，才可向用户报告全量；否则如实报告部分成果和最后卡点。
