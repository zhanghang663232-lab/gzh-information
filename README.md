# gzh-information

> **Mac 用户请先看这里：当前 `main` 是旧版归档脚本，不是桌面微信批量读取 v2。**  
> 不要从 `wechat-archive/scripts/archive_account.py` 开始验证 Mac v2；它依赖第三方导出服务的 API key，与当前目标技术路线不同。

## 选择正确版本

| 入口 | 状态 | 用途 |
| --- | --- | --- |
| [Mac v2 alpha 分支](https://github.com/zhanghang663232-lab/gzh-information/tree/codex/v2-macos-validated) | 当前试验版本，尚未合并到 main | 在已登录的 Mac 微信中，通过桌面界面分批读取公开文章；默认不需要导出服务 API key |
| [v2 验收记录](https://github.com/zhanghang663232-lab/gzh-information/blob/codex/v2-macos-validated/docs/acceptance/run-2026-09-27.md) | 已完成单轮 10 篇小样本 | 查看已验证与尚未验证的能力 |
| [草稿 PR #2](https://github.com/zhanghang663232-lab/gzh-information/pull/2) | 未合并 | 审阅 v2 代码与测试 |
| [旧版 wechat-archive](wechat-archive/README.md) | 仅保留作历史实验 | 公开专辑或导出服务路线；不代表 Mac v2 |

## 给人和 AI 助手的起点

1. 在 GitHub 页面切换到 **`codex/v2-macos-validated`** 分支，再阅读该分支的 [README](https://github.com/zhanghang663232-lab/gzh-information/blob/codex/v2-macos-validated/README.md) 与 [Agent 起步说明](https://github.com/zhanghang663232-lab/gzh-information/blob/codex/v2-macos-validated/docs/agent-start.md)。
2. Mac 用户从该分支的 **Code → Download ZIP** 获取源码；也可以使用已提供的 Apple Silicon 源码体验包。解压后先双击 `安装.command`，再双击 `启动.command`。
3. 首次在已登录的 Mac 微信中手动打开目标公众号主页，按图形向导先试跑少量文章。DeepSeek、豆包 API 是可选的 OCR 复核器，不会单独获得电脑操作能力。

**当前没有“粘贴链接就全量读完”的验收结论。** v2 只在一个目标账号验证了单轮新增 10 篇；文章列表末尾、全部互动字段与长批次稳定性仍待验证。请勿把小样本成功写成全量完成，也不要把第三方文章正文、Cookie 或模型密钥提交到仓库。
