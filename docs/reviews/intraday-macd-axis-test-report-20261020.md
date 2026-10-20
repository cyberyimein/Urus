# 日内 MACD 与 K 线共享时间轴验收

日期：2026-10-20\
范围：本地 Vite + Playwright 固定 fixture；没有连接生产 API、provider、OpenD、SSH 或容器。

## 测试内容

脚本为 [macd_axis_acceptance.mjs](../../output/playwright/urus-intraday-macd-axis-20261020/macd_axis_acceptance.mjs)。fixture 生成 36 根连续 5 分钟 K 线，并明确设置：

- MACD H 只有末 3 个有效值，前 33 个点为 `null`；
- `ΔH` 只有末 2 个有效值，前 34 个点为 `null`；
- `Δ²H` 只有末 1 个有效值，前 35 个点为 `null`；
- 有一个过零 marker，时间使用对应 5 分钟 bar 的 `bar_end_at`；
- 另测缺一根 K 线、三个序列全为空、390px 手机视口。

验收通过条件是：K 线和 MACD 共用首/中/末时间刻度与 x 坐标；有效 MACD 柱和 marker 落在对应 K 线中心；前置 `null` 不被补成零；缺口保持断线；全空序列仍保留时间轴并显示“样本不足”；窄屏无横向溢出。

## 修改前红灯

旧版视图由临时 Vite alias 加载 `/private/tmp/IntradayObservationView-before-shared-axis.vue`，结果为 **9 项失败**：

- 首/中/末时间标签不一致；
- 末 3 根 MACD 柱均未与对应 K 线中心对齐；
- marker 未与对应 K 线中心对齐；
- 缺口场景时间轴不一致；
- 全空序列没有空时间轴、样本不足说明或三个时间刻度。

红灯完整输出保存在 `/private/tmp/urus-macd-axis-red-20261020.log`，截图在 [artifacts-red](../../output/playwright/urus-intraday-macd-axis-20261020/artifacts-red/)；这是需求实现前的基线证据。

## 当前实现复验

当前视图使用共享时间轴实现后，脚本结果为 **通过**：

```text
passed: true
failures: []
15/15 fixture API requests intercepted locally
unmatchedApi: []
consoleErrors: []
pageErrors: []
```

15 次请求由 base、gap、empty 三种 fixture 各执行一轮五个只读接口组成，均被 Playwright route 拦截。green 输出保存在 `/private/tmp/urus-macd-axis-green-20261020.log`。

宽屏 H 图（含 K 线、H 末 3 柱及 marker）见 [macd-axis-h-wide.png](../../output/playwright/urus-intraday-macd-axis-20261020/artifacts-green/macd-axis-h-wide.png)；手机、全空序列截图见 [artifacts-green](../../output/playwright/urus-intraday-macd-axis-20261020/artifacts-green/)。红灯截图保存在 [artifacts-red](../../output/playwright/urus-intraday-macd-axis-20261020/artifacts-red/)。

测试结束后已停止本轮启动的 Vite 进程，`lsof -nP -iTCP:5192 -sTCP:LISTEN` 无输出。

## 未覆盖范围

本专项只验证浏览器渲染后的时间轴、有效点/marker 映射、缺口/空数据降级和手机布局；没有验证后端 MACD 数学计算、数据采集、真实行情或生产部署。
