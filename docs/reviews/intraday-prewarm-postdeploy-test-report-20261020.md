# 历史预热版部署后只读验收（2026-10-20）

本报告对应历史 5 分钟预热版部署后的独立 HTTP 验收。服务切换完成后执行了一次 LAN 只读 smoke；没有触发采集、provider 或任何写接口。

## 只读范围

脚本为 [`http_readonly_smoke.mjs`](../../output/playwright/urus-postdeploy-readonly-20261020/http_readonly_smoke.mjs)，只发 `GET`，目标默认是 `http://192.168.31.31:7777`。检查内容包括：

- `/api/health`、`/api/health/database`、`/api/version`、Universe、groups 和设置页相关只读接口；
- 首页、`/intraday`、`/settings/universe` HTML，以及页面实际引用的 JS/CSS 完整 body、Content-Length 和 MIME；
- `/api/intraday/collection-plan` 的 `2026-10-20` 与 `2026-10-21`；
- 新 plan 的 `asset_type_by_symbol`：成员键集合完整，类型值属于 `equity/etf/market/unknown`，SPY/QQQ/IWM 为非 `equity`，当前 46 标的快照中的 `equity` 数量为 31；
- 使用后端同样的 canonical sorted-key JSON + SHA-256 规则复算 `plan_id`；
- 既有 `/api/observation/collection-plan` 的最近完整收市日 `2026-10-19` 与未收市日期的结构化响应；
- `/api/intraday/overview`、events、health、systematic-risk 只读降级状态；
- 旧双图静态资源名称 `index-CQFXFf8n.js` 与 `index-DWN1bHP7.css`。

脚本会断言所有实际请求均为 `GET`，不会调用 provider、刷新、采集、PUT、POST、DELETE、SSH 或容器操作。

## 执行命令（待 root GO）

```text
TRADING_DATE=2026-10-20 \
NEXT_TRADING_DATE=2026-10-21 \
LEGACY_TRADING_DATE=2026-10-19 \
TARGET_URL=http://192.168.31.31:7777 \
OUTPUT_DIR=/private/tmp/urus-prewarm-postdeploy-20261020 \
node output/playwright/urus-postdeploy-readonly-20261020/http_readonly_smoke.mjs
```

脚本语法已通过 `node --check`。

## 最终只读结果

执行命令：

```text
TRADING_DATE=2026-10-20 \
NEXT_TRADING_DATE=2026-10-21 \
LEGACY_TRADING_DATE=2026-10-19 \
EXPECTED_EQUITY_COUNT=31 \
TARGET_URL=http://192.168.31.31:7777 \
OUTPUT_DIR=/private/tmp/urus-prewarm-postdeploy-20261020-final \
node output/playwright/urus-postdeploy-readonly-20261020/http_readonly_smoke.mjs
```

结果：**21 个 GET 请求，56/56 条检查通过，0 个失败**。默认沙箱首次运行因网络隔离得到
`TypeError: fetch failed`，没有形成服务结论；随后按已授权 LAN 路径重跑并通过。输出证据：
[`result.json`](</private/tmp/urus-prewarm-postdeploy-20261020-final/result.json>)。

关键响应：

- `/api/intraday/collection-plan?trading_date=2026-10-20`：200，`configured`，plan id
  `36bd10477ab4dfea5f0e6ebc64f9a8b23983141407ce8a86bc3bbfad7660cae3`；
- `2026-10-21` 新 plan：200，`configured`，plan id
  `0d1ef6b6c6ae613014d4c8619ab7cb3fb2c82cf67f18916413501a617651949e`；两者的
  `asset_type_by_symbol` 键集合与成员完整匹配，固定基准为非 equity，当前 equity 数量均为
  31，plan_id canonical SHA-256 复算一致；
- 旧 `/api/observation/collection-plan?trading_date=2026-10-19`：200，完整 plan，10 个 group；
- 旧接口对 10/20 和 10/21：结构化 409，说明尚未完成收市，最后完整交易日为 10/19，符合预期；
- `/api/intraday/overview`：200，`status=stale`，46 个标的；
- `/api/intraday/events?limit=2`：200，`status=ok`，返回 2 条事件；
- `/api/intraday/health`：200，但 `status=error`、`stale=true`、`age_seconds≈47964.68`、
  `stale_after_seconds=600`，保留真实 `last_error=无法只读 sidecar 调度槽：attempt to write a readonly database`；
- `/api/systematic-risk/overview`：200，`status=unavailable`，3 个 ETF 结构化不可用结果；
- `/`、`/intraday`、`/settings/universe` 均返回 575 bytes HTML；JS 为 371,156 bytes，CSS 为
  159,915 bytes，Content-Length 完整匹配，资源名为 `index-CQFXFf8n.js` 和
  `index-DWN1bHP7.css`。

## 线上状态记录

本轮没有调用 PUT、POST、DELETE、刷新、采集、provider、SSH 或容器操作。`/api/intraday/health`
的历史只读数据库错误被如实记录，没有因为 smoke 失败请求线上修改或清除告警。
