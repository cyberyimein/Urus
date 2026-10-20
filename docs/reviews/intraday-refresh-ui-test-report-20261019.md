# 日内观察页刷新与 MA5 对齐验收（2026-10-19）

本报告记录本轮前端改动的独立浏览器验收。测试只使用本机 loopback 的 Vite
开发服务器和 Playwright route 拦截的固定 fixture；没有访问生产 API、OpenD、SSH
或容器，也没有写入业务数据库。

## 测试方法

- 页面：`/intraday`。
- fixture：`output/playwright/urus-intraday-refresh-ui-20261019/fixture.mjs`。
- 脚本：`output/playwright/urus-intraday-refresh-ui-20261019/baseline_refresh_ma5.mjs`。
- 允许的 API route：`/api/health/database`、日内 overview/events/health、
  systematic-risk overview；任何未列出的 `/api` 请求都会让脚本失败。
- 浏览器时间固定为 `2026-10-16T14:59:30Z`，通过 Playwright clock 快进到
  5 分钟边界，比较各 route 的实际请求次数。
- Playwright CLI wrapper 因本机 npm cache 没有 `@playwright/cli` 元数据而无法离线
  启动；使用本机已缓存的 Playwright Node runtime 1.64.0-alpha 和系统 Chrome
  执行同一浏览器级脚本。未联网安装依赖。

## 修复前红灯证据

命令使用本机 Vite（`127.0.0.1:5190`）及上面的脚本，结果保存在
`output/playwright/urus-intraday-refresh-ui-20261019/baseline-result.json`，截图为
`baseline-refresh-ma5.png`。

结果为 **failed**：

- 5 分钟边界前 29 秒，`/api/intraday/events` 和 `/api/intraday/health` 各额外
  请求 1 次，证明子组件仍有 15 秒轮询。
- 到达边界时，overview/events/health/systematic-risk 四个核心请求均没有按整点
  刷新；旧实现使用 60 秒/15 秒 interval，而不是 5 分钟整点调度。
- 快进第二个 5 分钟槽后累计请求数为 overview `2`、events `3`、health `3`、
  systematic-risk `2`，再次暴露了旧轮询。
- MA5 首个有效点相对第 5 根蜡烛实体中心偏移 **11.000 SVG units**。
- `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`，因此上述失败不是
  fixture 路由或浏览器错误造成的。

## 修复后结果

修复后的原基线与扩展场景均通过。执行环境仍是本机 loopback Vite
`127.0.0.1:5190`、固定 fixture 和 Playwright clock；Vite 进程由测试脚本启动并在
退出时终止，测试结束后该端口没有监听进程。

原基线结果：

- `green-baseline-result.json` 为 `passed`。
- 初始加载后，每个核心接口在两个连续墙钟槽各只请求一次：
  `/api/intraday/overview`、`/api/intraday/events`、`/api/intraday/health`、
  `/api/systematic-risk/overview` 最终均为 `3`（初始加载 + 两个整点刷新）。
- 整点前 29 秒没有自动请求；两个整点均各产生一轮四接口读取。
- `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`。

扩展结果：

- `final-result.json` 为 `passed`，脚本为
  `output/playwright/urus-intraday-refresh-ui-20261019/refresh_ui_acceptance.mjs`。
- 手动刷新触发 overview、events、health、systematic-risk 各一次，并保留已选
  `SMH` 与事件策略筛选 `S2`。
- 将页面设为 hidden 后快进一个整点没有请求；恢复可见后恰好补一次四接口读取。
- 通过运行中的 Vue Router 临时空路由卸载 `/intraday` 视图后再快进一个整点没有
  残留请求，且 `.intraday-page` 已从 DOM 消失，证明父级 timeout 已清理；没有用
  `about:blank` 代替 SPA 卸载检查。
- 脚本在内存 fixture 中移除一根 5 分钟 bar；MA5 首点与对应蜡烛中心误差不超过
  `0.1 SVG unit`，且 MA5 SVG 被拆成至少两段，没有跨缺口连线。
- 三个 MACD tab 均可切换；marker title 含 ET 时间及正负转换标签；页面没有
  `NaN` 或 `undefined`。
- 扩展脚本的总请求为 overview `5`、events `6`、health `6`、systematic-risk
  `5`，其中多出的 events/health 是明确的 S2 筛选读取，不是定时器轮询。
- 截图：`refresh-full.png`、`refresh-candle-ma5.png`、
  `refresh-macd-markers.png`。

为排除仓库默认 Vite 配置中指向 `127.0.0.1:8000` 的开发 proxy 干扰，扩展脚本又用
`/private/tmp/urus-refresh-vite-config.mjs` 在 `127.0.0.1:5191` 启动了无 proxy 的
临时 Vite 配置，结果保存在 `final-isolated-result.json`，同样 `passed`，请求计数和
浏览器错误完全一致。默认配置的一次复跑日志曾出现 3 条本地 `ECONNREFUSED
127.0.0.1:8000` proxy 日志（overview、S2 events、systematic-risk），但这些请求
不在脚本收集的 route 列表中，浏览器结果仍通过；无 proxy 复跑消除了这类日志，最终
以无 proxy 结果作为离线验收证据。

前端构建也通过：

```text
npm --prefix frontend run build
vue-tsc --noEmit && vite build
84 modules transformed; built in 737ms
```

## 限制

本轮是固定 fixture 的浏览器行为验收，不能证明真实 API 的数据内容或生产网络链路。
Playwright CLI wrapper 因本机 npm cache 没有 `@playwright/cli` 元数据而无法离线启动，
所以使用同一已缓存 Playwright Node runtime 和系统 Chrome 执行浏览器脚本；没有联网
安装依赖。所有请求均由脚本 route 拦截，未连接 provider、OpenD、SSH、生产 DB 或
部署环境。
