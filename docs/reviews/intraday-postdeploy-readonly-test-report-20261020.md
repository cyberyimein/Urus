# 部署后只读 HTTP smoke（2026-10-20）

状态：**已完成一次最终只读 smoke；HTTP/结构验收通过，运行状态有如下真实告警。**

本轮验收脚本为 [http_readonly_smoke.mjs](../../output/playwright/urus-postdeploy-readonly-20261020/http_readonly_smoke.mjs)。脚本只使用 `GET`，不会调用 PUT/POST/DELETE、刷新、采集、provider、SSH 或容器操作。默认目标地址是 `http://192.168.31.31:7777`，执行前由 root 明确确认服务已切换。

## 只读覆盖

- `/api/health`、`/api/health/database`、`/api/version`；
- Universe、Universe versions、observation groups；
- 新日内池 `/api/settings/intraday-pool`；
- 新 `/api/intraday/collection-plan` 与既有 `/api/observation/collection-plan`，分别检查当前 XNYS 交易日和下一 XNYS 交易日；
- `/api/intraday/overview`、`/api/intraday/events?limit=2`、`/api/intraday/health`、`/api/systematic-risk/overview`；
- `/`、`/intraday`、`/settings/universe` 页面 HTML；
- 从页面 HTML 实际解析出的同源 JS/CSS，检查完整 body、`Content-Length` 和 MIME；
- 每个 JSON 响应检查状态码、JSON `Content-Type` 和基本结构；页面资源响应检查完整 body；所有实际请求最终断言为 GET。

当前和下一交易日可通过 `TRADING_DATE`、`NEXT_TRADING_DATE` 环境变量显式传入，避免在非交易日猜日期。本次使用 root 确认的 `2026-10-19` 和 `2026-10-20`。

## 执行方式

```bash
TRADING_DATE=YYYY-MM-DD \
NEXT_TRADING_DATE=YYYY-MM-DD \
TARGET_URL=http://192.168.31.31:7777 \
OUTPUT_DIR=/private/tmp/urus-postdeploy-readonly-20261020 \
node output/playwright/urus-postdeploy-readonly-20261020/http_readonly_smoke.mjs
```

脚本会在临时目录保存 `result.json` 和静态资源响应副本；不会修改仓库或服务端数据。执行结果保存在 `/private/tmp/urus-postdeploy-readonly-20261020/result.json`，终端日志在 `/private/tmp/urus-postdeploy-readonly-20261020-rerun.log`。

## 本次执行结果

执行目标为 `http://192.168.31.31:7777`，共发出 20 个请求，全部为 `GET`。最终脚本结果为：

```text
passed: true
failures: []
all requests are GET: true
```

页面与静态资源完整传输通过：

| 资源 | HTTP | 实际 body |
| --- | ---: | ---: |
| `/`、`/intraday`、`/settings/universe` | 200 HTML | 每页 575 bytes |
| `/assets/index-DD69aq0o.js` | 200 | 366,498 bytes，Content-Length 一致 |
| `/assets/index-e9t7FUvF.css` | 200 | 159,685 bytes，Content-Length 一致 |

既有和新接口均返回 JSON，状态/结构检查通过。新日内 collection plan 在 `2026-10-19` 和 `2026-10-20` 均为 `200 configured`；新池为 `configured`、revision `0`；Universe 为 revision `12`、46 个标的。旧 `/api/observation/collection-plan` 在 `2026-10-19` 返回 200；下一交易日 `2026-10-20` 返回结构化 `409 observation_collection_plan_invalid`，消息为“尚未完成收市；当前最后完整交易日为 2026-10-19”。这是旧接口要求收市完成时的预期拒绝，未按 HTML/500 处理。

盘中接口的真实运行状态如下：

- `/api/intraday/overview`：HTTP 200，`status=stale`，包含 46 个标的；没有把陈旧数据伪装为 fresh。
- `/api/intraday/events?limit=2`：HTTP 200，`status=ok`，返回 2 条事件。
- `/api/intraday/health`：HTTP 200，但 `status=error`、`stale=true`、`age_seconds≈13602.56`、`stale_after_seconds=600`，`last_error=无法只读 sidecar 调度槽：attempt to write a readonly database`。这是运行状态告警，已如实保留；本轮没有请求修改生产环境。
- `/api/systematic-risk/overview`：HTTP 200，`status=unavailable`，包含 SPY/QQQ/IWM 的结构化不可用结果；没有伪造 CTA 基线或情景。

未调用 PUT、POST、DELETE、刷新、采集、provider、SSH 或容器操作。
