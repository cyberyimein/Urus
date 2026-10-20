# 日内标的池设置页浏览器验收（2026-10-20）

本轮使用本机独占 Vite（`127.0.0.1:5192`）和 Playwright route fixture，未连接生产 API、OpenD、SSH、容器或数据库。所有 `/api` 请求均由脚本拦截；未匹配请求直接失败。

脚本：

```text
output/playwright/urus-intraday-pool-settings-20261019/universe_settings_acceptance.mjs
```

运行命令：

```text
node output/playwright/urus-intraday-pool-settings-20261019/universe_settings_acceptance.mjs
```

最终结果：`passed: true`，退出码 `0`。

通过范围包括：

- 从日内页的设置链接进入 `/settings/universe#intraday-pool`，hash 与设置区域正确。
- 当前配置和待生效配置的日期分别显示 `2026-10-20`、`2026-10-21`。
- 业务分组展示、按代码搜索 `SMH`、按分组搜索“芯片”均通过。
- `SPY`、`QQQ`、`IWM` 始终勾选且 disabled。
- 修改后撤销可恢复 pending 选择。
- 保存请求使用当前 `base_revision=7`，`selected_symbols` 为
  `AAPL/SMH/MSFT`，不包含固定基准；PUT 返回后 active 成员保持不变，只有 pending 更新。
- 409 revision conflict 显示冲突提示并锁定编辑；“重新读取”重新 GET，读取到服务端新的
  `2026-10-22` pending 版本并恢复编辑。
- `unconfigured`（无 active、无 pending）状态显示“请为下一交易日选择标的”，固定基准仍为
  checked/disabled，保存按钮可用，PUT 使用 `base_revision=10` 且发送 `selected_symbols=[]`。
- 390px 窄屏无横向溢出，标的池分组区域保持独立纵向滚动。

实际请求计数为 15 个，其中日内页和设置页的 health/overview/events/risk 请求均来自本地
fixture；设置页包含 3 次 GET 和 3 次 PUT（正常保存、409 冲突、unconfigured 空选择）。

`unmatchedApi=[]`、`pageErrors=[]`、`consoleErrors=[]`。浏览器对预期的 409 响应产生的一条
Chrome “Failed to load resource” 日志单独记录为 `expectedConsoleErrors`，不作为页面错误。

截图：

```text
output/playwright/urus-intraday-pool-settings-20261019/universe-settings-artifacts/universe-settings-desktop.png
output/playwright/urus-intraday-pool-settings-20261019/universe-settings-artifacts/universe-settings-mobile.png
```

测试结束后已停止本轮启动的 Vite 进程，端口 `5192` 无监听。

设置页验收后又复跑既有日内主页面脚本 `pool_settings_acceptance.mjs`，结果仍为
`passed`：宽屏池宽 `216px` 且可滚动，窄屏无横向溢出，5 分钟刷新/MA5 缺口回归通过，
摘要高度 `191.375px`（缺失数据仍为 `191.375px`），四个核心接口均为 `5/5/5/5`，
无未匹配 API 或页面错误。该轮本地 Vite 进程同样已清理。
