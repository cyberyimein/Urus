# MACD 双 pane 部署后只读 HTTP smoke（2026-10-20）

本轮针对 API/UI 切换 `urus-api-macd-overlay-20261020` 执行一次部署后只读回归。root 已明确 GO；本脚本只发 GET，不调用 provider，不触发采集，不发送 PUT/POST/DELETE，不修改配置、容器或数据库。旧版部署报告 [intraday-postdeploy-readonly-test-report-20261020.md](intraday-postdeploy-readonly-test-report-20261020.md) 保留不覆盖。

## 执行范围

- 目标：`http://192.168.31.31:7777`
- 美东日期：`TRADING_DATE=2026-10-20`、`NEXT_TRADING_DATE=2026-10-21`
- 脚本：[http_readonly_smoke.mjs](../../output/playwright/urus-postdeploy-readonly-20261020/http_readonly_smoke.mjs)
- 输出：[result.json](/private/tmp/urus-postdeploy-macd-overlay-20261020/result.json)
- 最近完整日补充响应：[legacy-2026-10-19.json](/private/tmp/urus-postdeploy-macd-overlay-20261020/legacy-2026-10-19.json)
- 日志：`/private/tmp/urus-postdeploy-macd-overlay-20261020.log`

主 smoke 共发出 20 个请求，全部为 GET，脚本结果 `passed=true`、`failures=[]`；随后补发 1 个 GET 验证最近完整日旧计划，因此本轮合计 21 个 GET。覆盖 health/database/version、Universe/versions/groups、intraday pool、当前/下一日新 collection plan、当前/下一日旧 observation collection plan、intraday overview/events/health、systematic risk、首页/日内页/设置页 HTML，以及页面实际引用的 JS/CSS。

## 结果

| 检查 | 结果 |
| --- | --- |
| `/`、`/intraday`、`/settings/universe` | HTTP 200，HTML 各 575 bytes |
| `/assets/index-CQFXFf8n.js` | HTTP 200，371,156 bytes，Content-Length 完整；SHA-256 `b653b33b3883c50b85d07b8bb8f8ab43a2e7870292e6c80fa8b372fbb48065a1` |
| `/assets/index-DWN1bHP7.css` | HTTP 200，159,915 bytes，Content-Length 完整；SHA-256 `e1770f438159e227e326aea9db727cddc4cb00a98d50b6082a2e03a1e707035d` |
| 新 intraday plan 2026-10-20 | HTTP 200，`configured`，plan id/schema 完整 |
| 新 intraday plan 2026-10-21 | HTTP 200，`configured`，plan id/schema 完整 |
| 旧 observation plan 2026-10-19 | 补充 GET 返回 HTTP 200，schema/plan/group ids 完整；plan id `19f7b26f…` |
| 旧 observation plan 2026-10-20/21 | 当前未收市，未来日期返回结构化 HTTP 409 `observation_collection_plan_invalid`，消息指出最后完整交易日为 2026-10-19 |
| `/api/intraday/overview` | HTTP 200，`status=stale`，46 个标的；陈旧状态保持显式 |
| `/api/intraday/events?limit=2` | HTTP 200，`status=ok`，2 条事件 |
| `/api/systematic-risk/overview` | HTTP 200，`status=unavailable`，3 个 ETF 结构化返回 |

下载后的新 JS 内容包含双 pane 关键 class：

- `intraday-macd-histogram-chart`
- `intraday-macd-derivative-chart`
- `intraday-macd-signal-marker`

## 运行告警

`/api/intraday/health` 返回 HTTP 200，但运行状态仍为：

- `status=error`
- `enabled=true`
- `stale=true`
- `age_seconds≈45542.60`
- `stale_after_seconds=600`
- `last_error=无法只读 sidecar 调度槽：attempt to write a readonly database`

该告警已原样保留，没有为了通过 smoke 而隐藏或修改生产状态。页面资源、API JSON 结构和新 MACD 静态资源检查均通过。

执行期间没有调用 provider，没有刷新/采集操作，没有 SSH 或容器操作；本轮只读 HTTP 请求结束后未留下本地服务进程。
