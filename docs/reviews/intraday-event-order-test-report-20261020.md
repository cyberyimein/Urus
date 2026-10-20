# 盘中事件面板布局验收

日期：2026-10-20\
范围：本地 Vite + Playwright 固定 fixture；没有连接生产 API、provider、OpenD、SSH 或容器。

## 验收内容

脚本为 [event_order_acceptance.mjs](../../output/playwright/urus-intraday-event-order-20261020/event_order_acceptance.mjs)。fixture 提供 42 条 SPY 盘中事件，并给每条事件设置冻结确认线、冻结失效线，覆盖：

- 广度、事件面板、标的池工作区的视觉/DOM 顺序；
- 长事件列表在自身容器内滚动，容器最大高度不超过 280px；
- 选中 SPY 后确认线和失效线仍绘制在 K 线图上；
- 390px 窄屏无横向溢出；
- `no_data` 与 `disabled` overview 仍显示事件面板；
- 初始读取中 overview、events、event health、systematic risk 各只请求一次。

## 修改前红灯

用临时 Vite alias 加载旧版视图与样式快照后，得到 2 项失败：

- 事件面板位于 workspace 之后，顺序不满足 `breadth < events < workspace`；
- 事件列表 `max-height` 为 480px，不满足 280px 上限。

红灯日志：`/private/tmp/urus-event-order-red-20261020.log`。截图见 [artifacts-red](../../output/playwright/urus-intraday-event-order-20261020/artifacts-red/)。

## 当前实现复验

当前实现结果为 **通过**：

```text
passed: true
failures: []
unmatchedApi: []
consoleErrors: []
pageErrors: []
```

共执行 20 次本地 fixture API 请求：base、`no_data`、`disabled` 各一轮，再回到 base 截图；每轮五个只读接口均被 Playwright route 拦截。42 条事件在 280px 内部滚动，SPY 冻结确认线和失效线均存在，窄屏检查通过。

当前实现截图：

- [桌面事件面板与工作区顺序](../../output/playwright/urus-intraday-event-order-20261020/artifacts-green/event-order-desktop.png)
- [390px 窄屏](../../output/playwright/urus-intraday-event-order-20261020/artifacts-green/event-order-mobile.png)

绿灯日志：`/private/tmp/urus-event-order-green-20261020.log`。

测试结束后已停止本轮 Vite 进程，`lsof -nP -iTCP:5192 -sTCP:LISTEN` 无输出。

## 未覆盖范围

本轮只验证事件面板布局、滚动、冻结线展示、状态降级和只读请求数量；没有验证事件后端计算、采集、生产数据库或部署流程。
