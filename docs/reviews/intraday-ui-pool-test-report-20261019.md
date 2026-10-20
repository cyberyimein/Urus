# 日内标的池与设置 UI 验收（2026-10-19）

本轮先记录开发前的最小浏览器红灯。测试只使用本机 loopback Vite 和 Playwright
route 拦截的固定 fixture，没有访问生产 API、OpenD、SSH、provider 或数据库。

## 测试方法

- 脚本：`output/playwright/urus-intraday-pool-settings-20261019/pool_settings_red.mjs`
- fixture：同目录 `fixture.mjs`；脚本在内存中给四个标的补入
  `observation_group`：大盘观察、板块观察、重点观察、轮换观察，用于验证页面不会
  只按 role 继续展示。
- 浏览器：本机已缓存的 Playwright Node runtime + Chrome；使用无 API proxy 的临时
  Vite 配置 `/private/tmp/urus-pool-settings-vite-config.mjs`，地址
  `127.0.0.1:5192`。
- 允许的请求只有 database health、intraday overview/events/health 和
  systematic-risk overview；任何未 mock 的 API 或外部请求都会记为失败。

## 当前实现红灯

命令结果保存在
`output/playwright/urus-intraday-pool-settings-20261019/pool-settings-red-result.json`，
退出码为 `1`，但 `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`，因此失败
来自页面行为而不是 fixture 或浏览器异常。

在 1440×1000 viewport 下发现：

- 四个 `observation_group` 值均未渲染。
- 标的池搜索输入缺失。
- 业务组折叠控件缺失。
- 标的池宽度为 **285px**，当前没有约 216px 的窄栏约束。
- 桌面标的池 `overflow-y=hidden` 且没有内部滚动区域。
- MACD 卡片缺少“柱状图”“斜率”“加速度”这三个常驻中文层级解释。

本次初始页面请求为 database health、systematic-risk、overview、events、health 各一
次，未进行刷新或写操作。

## 后续绿灯范围

待 UI 修改冻结后，用相同 fixture 扩展验收：窄栏内部滚动、搜索结果与空结果、业务组
展开/折叠及状态保持、设置链接、缺失值解释、MACD 三卡中文状态/斜率/加速度/正负
含义，并复跑上一轮 5 分钟刷新、MA5 中点对齐、缺口断线、MACD tabs/markers 和
无残留 timer 验收。

## 主 UI 绿灯结果

主页面实现冻结后，使用同一无 proxy Vite 配置和扩展脚本
`output/playwright/urus-intraday-pool-settings-20261019/pool_settings_acceptance.mjs`
复测，结果保存在 `pool-settings-final-result.json`，退出码为 `0`。

- 1440px 宽屏标的池实际宽度为 **216px**；业务组列表 `overflow-y=auto` 且在
  大组 fixture 下实际可滚动。
- 390px 窄屏无横向溢出，业务组列表仍独立可滚动。
- `大盘观察`、`板块观察`、`重点观察`、`轮换观察` 均按
  `observation_group` 展示；缺失该字段的 `LEGACY` 标的正确回退为
  `采集角色 · 轮换股`。
- 搜索 `SMH` 只保留匹配成员；无匹配时显示“没有匹配的标的”；清空搜索后恢复
  全部业务组，已展开的 `板块观察` 保持展开。
- 选中 `SMH` 后手动刷新，选中标的和业务组展开状态保持；设置链接为
  `/settings/universe#intraday-pool`。
- 报价/指标摘要默认折叠；MACD 三个卡片和 tab 有常驻中文名“柱状图状态”“斜率”
  “加速度”，且显示正负解释。
- 5 分钟整点刷新、边界前无请求、MA5 与蜡烛中心对齐、缺口断线均通过；本轮
  核心请求计数为 overview/events/health/systematic-risk `3/3/3/3`。
- `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`。

本轮还重新运行了既有刷新/MA5 基线脚本，结果为 `passed`，证据为
`output/playwright/urus-intraday-pool-settings-20261019/refresh-regression-result.json`。
宽屏、窄屏和 MACD 截图分别为 `pool-settings-wide.png`、
`pool-settings-narrow.png`、`pool-settings-macd.png`。

前端构建通过：

```text
npm --prefix frontend run build
vue-tsc --noEmit && vite build
84 modules transformed; built in 652ms
```

所有浏览器验收仍只在本机 loopback、固定 fixture、无 proxy 临时 Vite 上进行；测试
进程已清理。

## 摘要压缩与缺失值复验（2026-10-20）

按复核要求再次运行同一隔离脚本，结果仍为 `passed`，证据为
`output/playwright/urus-intraday-pool-settings-20261019/pool-settings-final-result.json`。
在 1440px 宽屏下，从详情标题顶部到 MACD 卡片顶部的实际渲染高度为 **191.375px**；
将选中标的改为报价全缺失、五项指标均缺失并注入长原因文本后，该高度仍为
**191.375px**，低于 200px 预算。报价摘要继续默认折叠并显示“来源时间未知”，
指标长原因只有展开“指标说明与缺失原因”后才出现，未把首屏摘要撑开。

本次脚本仍硬断言四个核心 API 的周期请求、宽窄屏池滚动、分组/搜索状态、MACD
中文层级、MA5 缺口和无未 mock 请求；最终计数为 overview/events/health/systematic-risk
`5/5/5/5`，`unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`。该轮使用的
Vite 服务绑定 `127.0.0.1:5192`，运行结束后已停止。
