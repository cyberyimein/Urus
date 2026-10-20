# 盘中事件上移

日期：2026-10-20。本地 UI 改修，未部署。

用户反馈盘中事件位于整段标的池/详情之后，被长列表埋没。页面现在按“行情状态 → 观察池广度/数据提示 → 盘中事件 → 标的池和个股图表 → CTA 风险”排列。

改动仅涉及 `frontend/src/views/IntradayObservationView.vue` 与 `frontend/src/styles/intraday.css`。事件列表最大高度由 480px 调整为 280px，超出部分内部滚动，避免事件多时再次挤走个股图表。原有筛选、标的联动、冻结线回传及刷新周期保持原实现；overview 为 no_data/disabled/invalid_source 时仍保留事件面板。

验证：vue-tsc 与 Vite 构建通过（88 modules），git diff --check 通过。Luna Playwright 本地 fixture 验收通过：修改前 2 项失败，当前 breadth < events < workspace 顺序正确；42 条事件在 280px 内部滚动，所选 SPY 冻结确认线和失效线保留，no_data/disabled 时仍有事件面板，390px 窄屏无横向溢出。20 次固定 fixture 请求全部拦截，无 console/page error 或未 mock 请求；每轮四个核心接口各一次。根代理已目视桌面截图。详细证据见 `intraday-event-order-test-report-20261020.md`；本轮 Vite 已停止。

未连接生产、未调整采集器或数据库、未部署。
