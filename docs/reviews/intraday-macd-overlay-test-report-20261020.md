# MACD 双 pane 叠加布局 Playwright 验收报告（2026-10-20）

本轮验收针对新的 MACD 页面布局：上方 H 柱状图，下方将 ΔH 与 Δ²H 叠加为两条曲线；三项过零 marker 统一锚定到对应的 H 柱体。测试仅使用本机 Vite（`127.0.0.1:5192`）与 Playwright route fixture，没有访问生产 API、provider、SSH、容器或生产数据库。验收代理未修改应用源码。

旧三 lane 版本的历史验收证据保留在 [intraday-macd-stacked-test-report-20261020.md](intraday-macd-stacked-test-report-20261020.md)，本报告只记录双 pane 版本。

## 验收脚本与 fixture

- 验收脚本：[overlay_macd_acceptance.mjs](../../output/playwright/urus-intraday-macd-overlay-20261020/overlay_macd_acceptance.mjs)
- 演示截图脚本：[preview_macd_card.mjs](../../output/playwright/urus-intraday-macd-overlay-20261020/preview_macd_card.mjs)
- 基础 fixture 使用 36 根 5 分钟 K 线，H 只有末 3 根有效、ΔH 只有末 2 根有效、Δ²H 只有末 1 根有效；有效值覆盖负值、正值和精确零，并包含三项 marker。
- 场景包含：基础缺失序列、删除中间 H/ΔH 点的 gap、删除末根 H 的 `missing-h`、全部 null 的 `empty`、390px 窄屏。
- 所有 `/api/*` 均由脚本拦截；未匹配 API 会 abort 并计入失败。

## 双 pane 验收结果

执行 `overlay_macd_acceptance.mjs` 后：`passed=true`，25/25 个本地 fixture GET 请求通过（base、gap、missing-h、empty，以及用于窄屏截图的 base 回载）。

已验证：

- 上 pane 只有一张 H 柱状图，下 pane 只有一张 ΔH/Δ²H 叠加图；旧 `.intraday-macd-lane` 三行结构不存在。
- H 有效柱严格为 3 根；ΔH 有两个有效点并连接成一段折线；Δ²H 只有一个有效点，显示 singleton dot，不生成折线。
- 两条差分曲线有独立图例（ΔH 斜率、Δ²H 加速度），并显示“量纲不同，不直接比较强弱”的说明。
- H、ΔH、Δ²H 三项 marker 全部只出现在 H pane：圆形对应 H、三角形对应 ΔH、菱形对应 Δ²H；marker 的 x 坐标等于最后一根 H 柱中心，y 坐标落在该柱体范围内。
- H 转正 marker 使用正值颜色，ΔH/Δ²H 归零 marker 使用中性颜色；marker title 保留同柱全部信号说明。
- 缺失末根 H 时不生成 marker；中间 gap 时 ΔH 两侧点各自为 singleton，不跨缺口连线；全空时不造柱、线、点或 marker，并保留两个 pane 的“样本不足”说明。
- H pane、差分 pane 与 K 线使用相同的美东时间刻度和 x 坐标；H 柱和两条曲线点与相应 K 线中心对齐。
- 三项原始值摘要同时可见，精确零值保持 6 位显示；390px viewport 无横向溢出。
- `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`。

宽屏 DOM 量测（1440px viewport）：

- MACD 卡片：918 × 507.78 CSS px（截图 918 × 509 px）。
- H pane：180.39 CSS px；差分 pane：156.39 CSS px。
- 窄屏截图：390 × 3140 px，无横向溢出。

验收日志与截图：

- `/private/tmp/urus-macd-overlay-green-20261020.log`
- [双 pane 宽屏截图](../../output/playwright/urus-intraday-macd-overlay-20261020/artifacts-green/macd-overlay-desktop.png)
- [双 pane 窄屏截图](../../output/playwright/urus-intraday-macd-overlay-20261020/artifacts-green/macd-overlay-mobile.png)

## 成熟波形演示卡片

另用 `preview_macd_card.mjs` 生成了只用于视觉预览的成熟 fixture：提供 2 根前置 H 样本，按相邻差分导出完整当前窗口的 H/ΔH/Δ²H，并生成多个确定性过零 marker；预热徽标设为 `100 / 100`。该脚本结果为 `demo_only=true`、`passed=true`，只执行 5 个本地 GET，不能作为真实数据或数值回归证据。

- [成熟波形双 pane 卡片预览](../../output/playwright/urus-intraday-macd-overlay-20261020/artifacts-preview/macd-overlay-preview.png)
- `/private/tmp/urus-macd-overlay-preview-20261020.log`

预览截图为 918 × 509 px，展示了完整 H 柱序列、蓝色 ΔH 曲线、金色 Δ²H 曲线以及多组 H/ΔH/Δ²H marker；当前值和 marker 说明已改为中文（例如“负：快线低于信号线”“由负转正”）。测试完成后已停止本轮启动的 Vite 进程，`127.0.0.1:5192` 无监听进程。
