# 日内页五分钟边界刷新与 MA5 对齐（本地待部署）

日期：2026-10-19。用户要求每个5分钟边界刷新页面，并修复蜡烛与MA5横向偏移。本轮只改本地前端，未部署、重启、修改生产配置或连接生产行情。

## 行为

- 打开页面立即读取；此后按美东钟表的 :00／:05／:10 等5分钟边界统一读取行情、事件、消费者健康和CTA数据。采用一个页面定时器，每次重新计算下一个绝对时间边界，不从打开页面的时刻累计5分钟。
- 页面只更新数据，不整页重载，保留有效的股票选择和筛选条件。手动刷新同步更新四个接口；用户主动修改事件筛选时仍立即读取对应结果。
- 隐藏标签页跳过定时读取，恢复可见时补一次并重新对齐；离开页面撤销定时器、可见性监听和进行中的读取。
- 事件面板原15秒轮询、行情与CTA原60秒轮询均已移除，两个子面板由父页面统一触发。
- MA5点和对应蜡烛都绘制在相同5分钟区间的中心，消除原半根K的横向偏移。只改变显示坐标，不改变MA5数值、K线真实起止时间或缺口断线规则；指标依然根据已收盘数据计算。

改动文件：`frontend/src/views/IntradayObservationView.vue`、`frontend/src/components/IntradayEventPanel.vue`、`frontend/src/components/SystematicRiskCard.vue`。

## 性能与时序说明

单个持续可见页面、无手动操作时，原定时请求理论为每小时600次：行情60＋CTA60＋事件／健康各240。新方式12轮×4接口＝48次／小时，下降92%；首次加载、手动刷新、筛选和恢复标签页另计。多个可见标签页分别计数。本次未做CPU／内存基准测试，不能把请求数当作实际资源占用测量。

所有接口只读取已采集数据，不增加OpenD请求，不改变1＋3＋1窗口、Eoflora优先级或采集时刻。5分钟整点刷新不等于整点实时采集。

采集正常时，T截止的数据通常在T＋4分钟完成整槽，页面在T＋5分钟读取，因此相对券商最新已收盘K通常落后一根；券商还显示当前形成中的K时，视觉上可能多两根。页面数据截止时间通常落后当前约5–10分钟，另有处理／网络耗时。采集失败、历史缺口或标签页暂停时不能保证只差一根。

## 验证与发布范围

独立Playwright脚本先复现旧实现：边界前额外请求、边界时四接口没有刷新、MA5横向偏移11个fixture SVG单位；证据 `output/playwright/urus-intraday-refresh-ui-20261019/red-baseline-result.json`。

`npm run build` 已通过 vue-tsc 与 Vite（84 modules）；`git diff --check`通过。浏览器最终验收见 [独立UI报告](intraday-refresh-ui-test-report-20261019.md)，没有增加前端单元测试。

独立Playwright验收通过：两个连续整点周期四接口各一次、边界前无额外轮询、手动刷新保留SMH／S2筛选、隐藏跳过与恢复补读、同一SPA路由卸载后无残留请求、MA5中心对齐与缺口断线、三个MACD tab及过零标记。最终隔离运行无未mock API、console error或page error；使用本机缓存Playwright运行时和本地无proxy Vite配置，没有访问生产API或真实provider。证据 `output/playwright/urus-intraday-refresh-ui-20261019/final-isolated-result.json`；root另查看了蜡烛／MA5截图。测试Vite进程已清理。

这次前端修改与此前 [G1 NaN补丁](intraday-quote-nan-fix-20261019.md) 均未部署。G1补丁独立应用只需新collector镜像；若收盘后一并应用本次UI修改，还需重新构建包含前端静态资源的Urus API镜像。API业务逻辑和数据库结构不变，无新数据库迁移。不要直接使用10月17日旧归档作为本次新版本。
