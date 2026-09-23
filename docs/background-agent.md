# 后台 Agent 路线

2026-09-23 更新：用户已明确接受前台操作。当前试跑使用可见微信窗口；下文保留此前后台路线的实机排查记录，后台模式仍未通过验收。

## 结论

微信 macOS 版的公众号列表是自绘 WebView。在实机检查中，macOS Accessibility 树只显示窗口和窗口按钮，不显示文章卡片。因此：

- 宿主机上的纯 AX 后台点击不可用；
- 坐标点击、菜单复制链接和滚动需要可验证的像素路由；
- 不能用“窗口标题为公众号”当作账号身份，所有账号都共用该标题。

可尝试先验证 Cua Driver 的窗口级后台操作；若微信的自绘 WebView 不响应后台滚动或点击，再考虑把微信放到独立 macOS VM。VM 中的 Agent 正常前台操作，但不占用宿主机桌面。这两条路径尚未完成微信实机验收，不能称为已实现的后台采集。

## 2026-09-21 宿主机探针

- 官方安装器指向 GitHub Release；本机下载连续约三分钟无响应，已中止。`CuaDriver.app` 未安装，未改变系统权限和 shell 配置。
- 官方 `cua-driver==0.28.2` Python 包可以从 PyPI 安装到项目虚拟环境。使用 `CUA_DRIVER_RS_TELEMETRY_ENABLED=0`，只读 `list_apps` 成功发现正在运行的微信。
- `list_windows(on_screen_only=True)` 对微信返回零个窗口；允许非前台窗口后能找到微信窗口，但该窗口截图只有空白背景和窗口按钮，没有文章内容。其辅助功能快照为零个元素且标记 degraded。未执行后台点击或滚动，因为没有可核验的目标页面。
- 因此“驱动能列举进程和窗口”不等于“微信可后台采集”。当前宿主机后台路线未通过实机验收。

### 手动安装后的复测

- 用户下载的 `CuaDriver.app` 位于 `/Applications/cua-driver-rs-0.28.2-darwin-universal/`；签名和 Apple 公证校验通过，随后移动到 `/Applications/CuaDriver.app` 并注册 LaunchServices。版本 `0.28.2`，默认遥测已关闭。
- `permissions grant` 及后续只读状态确认辅助功能、屏幕录制、直接截图均已授予 `com.trycua.driver`。
- 微信 PID 5844 的文章窗口 ID 7690 被枚举为 `is_on_screen=false`。驱动返回 `background_input.exact_window.status=ax_unresolved`，三条后台输入路线均为 `refused`，原因为 `off_space_or_ax_unresolved`；截图仍不能证明文章内容可见。
- 因此没有执行点击、滚动或正文采集。不能仅因权限已授予就将宿主机后台模式升级为可用。

### 用户打开目标主页后的复测

- `defaults read com.apple.WindowManager GloballyEnabled` 返回 `1`：台前调度开启。用户切回 Codex 后，微信“公众号”窗口 `9511` 被系统呈现为约 `68×147` 的侧边缩略窗口；其截图只有被缩小的主页，不能可靠核验账号名和文章卡片。
- Cua Driver 对该窗口仍返回 `ax_unresolved`，后台 accessibility、window_pointer 和 pid_keyboard 均为 `refused`。未试点击或滚动。
- 当时的下一次后台验证前提是用户手动临时关闭台前调度，让微信完整窗口留在当前 Space 并被 Codex 遮挡；只在窗口尺寸、目标账号及后台路由均核验通过后做一次滚动。不得自动修改用户的台前调度设置。

### 关闭台前调度后的最终宿主机复测

- `GloballyEnabled=0`；微信“公众号”窗口 `9511` 位于当前 Space、尺寸 `600×800`，截图能确认账号名为“监所家属”。
- 尽管截图与窗口身份均有效，Cua Driver 对同一窗口仍报告 `ax_unresolved`，后台 accessibility、window_pointer、pid_keyboard 路线全部 `refused`。因此未执行滚动或点击。
- 这排除了“只因台前调度缩略图导致失败”的解释。Cua Driver 宿主机后台路径不满足采集前提。用户随后明确接受宿主机微信被前台接管，因此当前工作流改为可见窗口的前台续采；这不是后台采集能力通过验收。

## 参考项目与采用的逻辑

- [Access_wechat_article](https://github.com/yeximm/Access_wechat_article)：采用“真实微信交互触发 + 本地任务调度 + 结构化记录 + 异常状态”的分层思想。不复制其 CC BY-NC-SA 代码。
- [Cua Driver](https://github.com/trycua/cua)：采用窗口级截图、明确的 `pid + window_id` 目标、每步重新观察、后台失败显式拒绝和前台升级的路由思想。
- [OpenAdapt Capture](https://github.com/OpenAdaptAI/openadapt-capture)：采用窗口限定的视频/操作时钟、窗口丢失即失败、原始证据本地保留的设计。
- [Lume / Cua](https://github.com/trycua/cua)：候选的本地 macOS VM 管理层。
- [Tart](https://github.com/openai/tart)：候选的 Apple Virtualization.Framework macOS VM 运行时。

## 强制身份锁

每篇文章入库前同时满足：

1. 首次进入和恢复时，列表页页头账号名等于目标账号，并保存窗口身份；
2. 滚动后页头不可见时，不把“看不见页头”误判为账号不匹配，但窗口身份变化必须恢复；
3. 每篇文章底部作者行等于目标账号，复制的正文不能只是 URL；
4. 任何明确不一致都丢弃当前页，从用户给定的样例链接重建对象；连续三次恢复失败就停止；
5. “N 篇原创内容”只是下界线索，不等于所有公开文章数。仅靠达到该数值不得标记全量。

## 运行模式

### 当前：`foreground_resumable`

宿主机微信可见操作，每篇落盘，随时可中断续传。身份锁失败时拒绝入库。

### 目标：`isolated_macos_vm`

1. 一次性创建独立 macOS VM；
2. 用户仅在 VM 内登录微信一次；
3. VM 无图形窗口启动，通过窗口截图/键鼠驱动操作；
4. 主程序只接收结构化事件和已校验数据；
5. 目标账号不一致、窗口丢失、截图指纹不变或文章作者不匹配时 fail closed。

这一模式需要大约 25–35 GiB 的初始 VM 镜像、一次微信登录和屏幕录制/辅助功能权限。不得在没有用户确认的情况下自动安装或修改系统权限。
