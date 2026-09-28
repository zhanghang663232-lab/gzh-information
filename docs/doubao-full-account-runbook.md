# 豆包执行入口：单个公众号的整账号读取

这是本仓库当前的**执行任务**，不是成功声明。目标是读取一个指定公众号所有普通用户可发现的历史文章，并保存正文和可见互动数据。默认只通过已登录的 **Mac 桌面微信界面** 操作；不得改用导出网站、后台接口、代理或抓包来掩盖界面问题。模型 API 只是可选的 OCR 复核器，电脑点击由本机程序执行。

## 本次目标与边界

- 本次测试账号：`Netskao`；起始链接：`https://mp.weixin.qq.com/s/cleXgWkz9UlinSLeQdLefQ`。这是豆包 2026-09-27 报告中的样例，**运行前须在微信重新核对链接确实属于该账号**。若用户改给其他公众号，替换名称和链接，建立独立工作区，绝不混用旧数据。
- 最新豆包报告与工作区审计：主页显示 `1271 篇原创内容`，已真实保存 **1 篇**，其正文达到最低入库检查，阅读 5652、点赞 23、评论数 0。第二篇依次遇到 `account_mismatch`、`copy_link_failed`、`profile_restore_failed`；窄标签的菜单按钮可能不可见。代码已增加有界 OCR 复核与已验证文章页尾恢复路径，但**这些修复尚未通过新版微信实机复测**。
- `1271` 是界面声明数，不是已发现或已保存篇数。`collect`/`resume` 进程退出码为 0 也不表示全量完成。当前 `audit/coverage.json` 的正文 `ok` 仅通过最低长度检查，不证明每篇全文无遗漏。
- `oldLikeNum`、公开评论/回复明细和部分转发数仍是能力缺口。缺失必须记为未知，不能填 0。微信普通用户看不到的内容不属于可采范围。

## 开始前（只做一次）

1. 使用 **[`codex/v2-macos-validated`](https://github.com/zhanghang663232-lab/gzh-information/tree/codex/v2-macos-validated)** 分支；`main/wechat-archive` 是旧实验。阅读 [故障总账](incident-ledger.md) 和 [故障手册](troubleshooting.md)。保留仓库现有数据和其他人的未提交修改。
2. 在现有 Mac 上运行 `.venv/bin/gzh-reader doctor --visual`。只有微信已登录、目标窗口可见且画面可读，才开始。权限全绿不等于文章窗口能操作。
3. 查明豆包上次试跑所用的 `Netskao` 工作区路径，**继续用同一个路径**。如确实不存在，才选择新的输出父目录；程序会在其下建立 `Netskao/`。不要复制旧数据库到另一个同名目录，也不要清空失败收据。
4. 在桌面微信中核对示例文章与公众号主页都显示 `Netskao`。若窗口有多个同名标签，只操作当次能确认标题和账号的窗口。不要关闭未知窗口、接受小程序授权或猜菜单坐标。

## 执行顺序

### 门 1：先让一篇真正入库

在项目目录运行下面命令；把 `<输出父目录>` 替换为上一步找到的工作区父目录。若已有工作区，也可以直接运行后面的 `resume`。

```bash
.venv/bin/gzh-reader collect \
  --url 'https://mp.weixin.qq.com/s/cleXgWkz9UlinSLeQdLefQ' \
  --account-name 'Netskao' \
  --max-new-articles 1 \
  --output '<输出父目录>'
```

不要添加 `--max-articles`；它是**工作区累计上限**，会妨碍整账号目标。首次关闭模型复核和 Agent 模式，先隔离桌面链路。命令结束后检查 `<输出父目录>/Netskao/audit/human-agent-progress.json`、`human-agent-last-error.json`、`human-agent-action-trace.json`，再运行：

```bash
.venv/bin/gzh-reader audit --workspace '<输出父目录>/Netskao'
```

门 1 通过要求：本轮 `new_in_run >= 1`（历史已有 1 篇不能替代新复测）；至少一个真实且不同的 `mp.weixin.qq.com` URL；对应正文文件存在、标题与微信一致，人工抽查首尾段；账号身份无冲突。阅读/点赞/转发/评论计数分别标记有值、零或缺失。**任何一个失败就停在该步骤修复，不得用连续重试伪造通过。**特别先验证“复制链接”点击后剪贴板确实出现本篇真实 URL；OCR 正文成功不能替代链接成功。目标标签菜单按钮若不可见，就先减少无关标签或放大微信窗口；程序不得点击推算出来的坐标。

### 门 2：扩至 5 篇，再以每轮最多 10 篇续读

门 1 通过后，同一工作区执行：

```bash
.venv/bin/gzh-reader resume --workspace '<输出父目录>/Netskao' --max-new-articles 5
```

核对 5 篇的不同 URL、标题、正文首尾、各字段缺失。无串号、无重复误计、微信未退出登录后，再按每轮最多新增 10 篇继续：

```bash
.venv/bin/gzh-reader resume --workspace '<输出父目录>/Netskao' --max-new-articles 10
```

一轮结束后**先审计再决定是否开始下一轮**。仅当 `stop_reason=batch_new_limit`、`new_in_run>0`、账号仍登录且工作区无身份冲突时，才直接续下一轮。出现 `link_copy_failed`、`body_missing`、`profile_restore_failed`、`wechat_login_required`、`account_mismatch`、`article_not_opened`、小程序弹窗、连续新增 0 或窗口不可读时，停止循环并按故障手册定位。不要换号/IP、修改系统代理、自动重新登录或无限重复同一篇。批量导致退出登录的真实触发条件尚未证实。

每次修代码：先提交可复现的脱敏失败收据与单元测试，再修改一个故障步骤；运行 `.venv/bin/python -m pytest -q` 和 `git diff --check`。只把源码、测试、脱敏说明同步到 GitHub；数据库、正文、截图、Cookie、API key 不上传。

### 门 3：完整性验收，不得自行宣布完成

反复续读的目标不是“跑满 1271 次”，而是建立**去重后的完整列表证据**。最终至少核对：

1. 列表确实到达末尾，有可复查的末尾证据；主页原创数、重复视口或任务退出都不能单独证明列表完整。当前程序的 `human-agent-progress.json` 仍写 `complete=false`，因此必要时先修复可审计的列表完成判定，再谈全量。
2. `audit/coverage.json` 中去重文章数、有效正文数、终态不可得数和缺失记录一致；可访问文章正文目标覆盖率 ≥95%，并人工抽检 OCR 正文的首段、末段及长图/广告混入风险。
3. `readNum`、`likeNum`、`oldLikeNum`、`shareNum`、`commentNum` **逐字段**目标覆盖率 ≥98%；普通用户可见评论/回复明细另单独核对。当前程序未达到这些门槛时只能交付“已读取部分 + 明确缺失清单”，不能改口称全量。
4. 保存 `audit/coverage.json`、`human-agent-progress.json`、`checksums.sha256`、数据库和导出文件；安全扫描无密钥/Cookie，且无遗留代理状态。检查所有数据均属于同一账号。

如果微信界面、平台可见性或当前程序能力使任一门槛无法达到，向用户交付**实际篇数、覆盖率、最后失败步骤和可复现证据**，并列出下一项最小修复。不要把模型推断、主页总数、命令退出码或较早版本的 10 篇样本当成这次的全量结果。
