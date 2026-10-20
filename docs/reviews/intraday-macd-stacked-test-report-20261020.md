# MACD 三层同显 Playwright 验收报告（2026-10-20）

本轮验收针对个股日内详情页的 MACD H、ΔH、Δ²H 改为同层级纵向堆叠显示。测试只使用本机 Vite（`127.0.0.1:5192`）与 Playwright route fixture；没有访问生产 API、provider、SSH、容器或生产数据库。前端应用源码未由验收代理修改。

## 测试入口与 fixture

- 脚本：[stacked_macd_acceptance.mjs](../../output/playwright/urus-intraday-macd-stacked-20261020/stacked_macd_acceptance.mjs)
- fixture：复用 `output/playwright/urus-intraday-pool-settings-20261019/fixture.mjs`，由脚本在 route 内生成 36 根 5 分钟 K 线。
- 基础数据：H 只有末 3 根有效、ΔH 只有末 2 根有效、Δ²H 只有末 1 根有效；有效值包含负值、正值和精确 0，并带每行过零/归零 marker。
- 变体：完整序列、移除中间 K 线的 gap、全部 MACD 点为 null 的 empty、390px 窄屏。
- API：仅拦截 `/api/health/database`、`/api/systematic-risk/overview`、`/api/intraday/overview`、`/api/intraday/events`、`/api/intraday/health`；未匹配 API 会 abort 并计入失败。

## 测试先行红灯

旧 tabs 实现通过临时 Vite alias 加载备份源码 `/private/tmp/urus-macd-stacked-20261020/before/`，命令为：

```sh
UI_ARTIFACT=.../artifacts-red \
  node output/playwright/urus-intraday-macd-stacked-20261020/stacked_macd_acceptance.mjs
```

结果：`passed=false`，共 7 项失败，证明旧实现缺少本轮要求：仍渲染 tabs、只有一条图表、没有三层名称/独立轴，gap 与全空场景也没有三条同时存在的时间带，并将精确零值画成 1px。红灯日志：

- `/private/tmp/urus-macd-stacked-red-20261020.log`
- 红灯截图：`output/playwright/urus-intraday-macd-stacked-20261020/artifacts-red/`

## 当前实现验收

使用当前源码启动临时 Vite 后执行：

```sh
UI_ARTIFACT=.../artifacts-green \
  node output/playwright/urus-intraday-macd-stacked-20261020/stacked_macd_acceptance.mjs
```

结果：`passed=true`，15/15 fixture 请求通过（base、gap、empty 各 5 个 GET）。

已验证：

- 页面同时存在 3 个 `.intraday-macd-chart`，无 `tablist`/`tab` 结构。
- 三行名称、当前原始值、正负读法和 marker 同时可见；H、ΔH、Δ²H 的 raw 值没有互相覆盖。
- 每行独立对称纵轴；含非零值的行显示 `+max / 0 / −max`，全零行的 `+max` 与 `−max` 数学上合并为精确 `0` 轴。
- 每行仅绘制有效点，36 根输入得到 H/ΔH/Δ²H 分别 3/2/1 根柱；null 没有被补成 0。
- 每行的时间刻度 x 坐标与 K 线刻度相同；有效柱中心与对应 K 线中心一致；marker 落在对应末根 K 线中心。
- 精确零值柱的 SVG `height` 为 0，没有用 1px 制造假柱。
- 过零/归零 marker 与对应行绑定：H 的正向 marker 使用正值颜色，ΔH/Δ²H 的 `to_sign=zero` marker 使用中性颜色，不误导为负值。
- 缺口场景保留三行图表，K 线/MA5 缺口不被连接；全空场景保留三条时间带并显示“样本不足”说明。
- 390px viewport 无横向溢出。
- `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`。

宽屏 DOM 量测（1440px viewport）：

- MACD 卡片：918 × 607.5 CSS px（截图 918 × 608 px）。
- 三个 lane：各 154.5 CSS px。
- 窄屏截图：390 × 3255 px，无页面横向溢出。

绿灯日志与证据（压缩行高及 marker 颜色修订后）：

- `/private/tmp/urus-macd-stacked-green-20261020.log`
- [宽屏三层 MACD 截图](../../output/playwright/urus-intraday-macd-stacked-20261020/artifacts-green/macd-stacked-desktop.png)
- [窄屏截图](../../output/playwright/urus-intraday-macd-stacked-20261020/artifacts-green/macd-stacked-mobile.png)

测试结束后已停止本轮启动的 Vite 进程；`127.0.0.1:5192` 无监听进程。
