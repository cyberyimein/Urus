# 日内页面与标的池设置验收

日期：2026-10-20（Asia/Tokyo）；工作始于 10/19。本次范围为本地开发与离线验收，无部署、SSH、生产接口或真实 provider 调用。

## 实现

- 主日内页：216px 标的栏、业务分组折叠/搜索/内部滚动；无业务组的旧 capture 回退采集角色并标明含义。刷新保留选择与展开状态。
- 摘要：报价与数据来源时间、指标缺失原因可展开；宽屏标题至 MACD 约 191px，长缺失原因不撑高摘要。
- MACD：H 柱状图状态、ΔH 斜率、Δ²H 加速度的常驻中文解释、正负含义及当前 tab 公式。H= DIF−DEA，未引入 2 倍柱值口径。
- 设置：`/settings/universe#intraday-pool` 的独立日内池组件；显示当前和待生效配置、下一交易日日期、分组勾选和搜索、不可取消的三只基准、未保存提示/撤销、保存及冲突重新读取。与全局 Universe 表单分别保存。
- 数据链路：后端版本化 active/pending，按 XNYS 日历决定生效日；collector 在到期股票任务中冻结当天计划，维护/分片/合并共用，历史 capture 的分组不会随新设置重标。首次池来自实际旧 capture；旧池不可解析时显式失败，不悄悄用默认 18 代码替换 46 代码。

## 验收证据

主页面 Playwright 与摘要高度检查已通过，详见 `intraday-ui-pool-test-report-20261019.md`。设置页 Playwright 全部通过，详见 `intraday-pool-settings-ui-test-report-20261020.md`：实际验证 GET/PUT、active/pending、固定基准、空池仅保存三只基准、撤销、409 冲突重新读取和 390px 窄屏。无未 mock API、页面异常或非预期 console error。最终主页面回归仍通过 5 分钟刷新、MA5 中点对齐与缺口断线。

最终验证：

- Backend 全套：577 passed，62.24 秒。
- Collector 全套：183 passed，6.94 秒；包含 Eoflora 优先、旧任务回归、动态标的历史额度准入、NaN 报价隔离、分片与 finalizer 元数据一致性。
- 上述两套结果由指定开发对话流在严格离线 network guard、临时库及关闭真实 provider 的条件下执行；完整测试执行输出保存在该对话流，不另伪称有独立日志文件。
- 前端：`npm --prefix frontend run build`，vue-tsc 与 Vite 通过，88 modules；最终本地日志 `/private/tmp/urus-pool-settings-final-build.log`。
- Luna 独立编写的后端与 collector 回归包含在最终全套中；发现的 settings_response 变量名错误和计划不可用时 terminal=false 已修复。
- 日内主页面、设置页 Playwright 固定 fixture 验收全部通过；根代理已检查实际渲染截图。
- `git diff --check` 通过。

测试策略：后端/collector 用临时库和固定输入，禁止真实网络；页面用本机 loopback Vite、Playwright route 拦截固定 API，任何未 mock 请求作为失败。没有前端 UI 单元测试。

## 部署边界

这些改动尚未部署。现运行容器保持原样；浏览器截图的当前生产状态不会因本次本地代码自动变化。API 容器包含前端，后续部署需同时使用包含新设置接口和 UI 的 API 版本，以及支持冻结计划的 collector 版本。
