# 日内 MACD 与 K 线共享时间轴

日期：2026-10-20；仅本地修改，无部署或生产访问。

## 问题与调整

旧页面先过滤无效 MACD 值，再使用剩余点的首尾时间铺满图表。36 根完整 K 线首次计算 MACD 时，H 只有最后三根、ΔH 两根、Δ²H 一根，因此三个 tab 的时间尺度各不相同，也无法对齐下方 K 线。

修改 `frontend/src/views/IntradayObservationView.vue`，提取共享时间轴，以当前 K 线最早开始时间和最后结束时间确定范围。两个图使用相同左右边界、时间映射和首中末刻度。MACD 柱及过零标记用指标结束时间匹配对应 K 线中心；缺口保留真实时间间距，未预热位置保持空白，不补零、不改变计算公式或策略 100 根门槛。

没有有效 MACD 数值但存在时间范围时保留空图和坐标轴，并显示“样本不足，尚无可显示的指标值”。图下注明有效指标数量及空白含义。没有 K 线时使用完整 MACD 序列时间范围（包含 null 点）作为备用，不仅使用有效点。

纵轴仍分别使用指标值与价格单位；本次统一的是时间横轴。

## 验证

- `npm --prefix frontend run build`：vue-tsc 与 Vite 通过，88 modules。
- `git diff --check`：通过。
- Luna Playwright 专项：修改前同 fixture 9 项失败，当前实现全部通过。36 根 K 线下 H/ΔH/Δ²H 分别只有 3/2/1 根，均按真实时间定位；H 的三个柱中心及过零 marker 与对应 K 线中心对齐。
- 共享首/中/末时间刻度、K 线缺口保留、全空指标的空轴/说明、390px 手机视口均通过。15/15 API 请求被本地 fixture 拦截，无未 mock 请求、console error 或 page error。
- 根代理已目视当前宽屏截图，三根柱子在右侧与对应三根 K 线对齐。
- 详细报告：`intraday-macd-axis-test-report-20261020.md`；脚本与红绿截图：`output/playwright/urus-intraday-macd-axis-20261020/`。
- 本轮临时 Vite 已停止。

## 部署状态

尚未部署；运行容器保持原样。
