# Urus P3 盘中指标与只读观察测试报告（2026-10-15）

本轮先建立 P3 后端纯行为红灯，范围是从 P2 股票盘中 raw contract 计算完整 5 分钟证据和指标。测试只使用内存构造的 UTC 数据，不连接 OpenD、Moomoo、远程 API、SSH、生产 SQLite 或容器，也不写业务代码。

## 当前最小契约

测试先固定两个 provider-independent 入口，等待开发实现后再按最终等价命名收敛：

```python
aggregate_completed_bars(
    minute_bars,
    *,
    session_open,
    session_close,
    cutoff,
)

build_intraday_projection(contract, *, now)
```

输入分钟线必须使用 P2 最终格式中的 UTC aware `bar_start_at` 和 `bar_end_at`，测试不从旧 provider `bar_time` 推断时间区间。聚合以 regular session 开盘为锚，只输出 `bar_end_at <= cutoff` 且五根分钟线齐全、连续、有效的 5 分钟 K；缺分钟、冲突重复和非法 OHLCV 不前值填充，也不能压缩后跨缺口聚合。

projection 最小观察字段按个股 `symbols[SYMBOL]` 组织，包含：

- 完整 5 分钟 K；
- MA5、MA5 距离和斜率；
- Wilder ATR14；
- session VWAP 及分钟覆盖原因；
- MACD H、ΔH、Δ²H、对应序列、34/35/36 根数学可用边界及 100 根策略暖机状态；
- 以 SPY 为基准且同起止时刻对齐的 RS15/RS30；
- 缺基准或缺历史 RVOL 时返回不可用/null 和原因。

本轮也加入 `Settings.intraday_equities_enabled` 默认 `False` 的红灯，确保只读页面不会自动启用采集。

## 验收证据

测试文件：[backend/tests/test_intraday_equities_acceptance.py](../../backend/tests/test_intraday_equities_acceptance.py)

当前文件共 39 个独立用例；第 36 项使用真实 `capture_contract` 和 `CollectorStore.write_scheduled_raw` 写入临时 sidecar，专门验证 raw 行写入时间与 envelope `captured_at` 不应被 API 误判为不同证据，以及 scheduler task name 不应被硬编码。第 37–39 项固定 SDK quote `update_time` 的美东无时区解析、缺失和非法降级。

命令：

```text
cd /Users/waynewong/code/Urus
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_intraday_equities_acceptance.py
```

最新独立复测结果为 `39 passed`（仅现有 Starlette/httpx deprecation warning）。测试先行期间保留了 source/freshness/RS/breadth/API 红灯，未把尚未实现的行为隐藏掉。

结果包含 source ledger kind/task 校验、stored_at 与 envelope captured_at 分离、最新 partial/损坏槽选择、cutoff freshness、连续 RS15/RS30、breadth 有效分母、只读 sidecar、禁用/no_data、两个 API 路径及完整纯计算指标。quote 时间扩展也已通过：SDK `update_time`（例如美东 `2026-10-15 10:00:03`）转换为 UTC `2026-10-15T14:00:03+00:00`，缺失或非法值降级为 unknown。

已写入的红灯覆盖：

1. 以 UTC start/end 聚合开盘锚定的 5 分钟窗口，验证 OHLC、成交量和 cutoff；
2. cutoff 后的未来／不完整窗口丢弃；
3. 缺分钟不补齐、不跨缺口压缩；
4. 冲突重复与非法 OHLCV 使窗口不可用；
5. 完整 session VWAP 与缺分钟时不可用；
6. MA5 五根完整 5 分钟 K 暖机；
7. Wilder ATR14、MA5 距离和斜率的预热；ATR 使用 16 根 5 分钟 K 的独立 TR14 seed 加两次 Wilder 递推，并与末 14 根简单平均区分；MA5 在 25 分钟可用、斜率到 30 分钟才可用且按独立均值差校验；
8. MACD 复用 P1 核心，H/ΔH/Δ²H 在第 34/35/36 根分别可用；
9. 当日最多 78 根时 MACD 数值可计算但 100 根策略暖机仍未完成；
10. RS15/RS30 按 symbol 与 SPY 的共同 UTC 起止时间对齐；
11. 缺 SPY 或缺同时间段 RVOL 历史时保持 null/unavailable，不补零；
12. 非有限／负输入不能发布 ready 指标；
13. 最新 5 分钟窗口尾部缺口不能把旧值伪装成当前 cutoff，必须显式 stale/unavailable/partial 并提供早于 cutoff 的 `as_of`；MACD 信号不能跨尾部缺口；
14. RS15/RS30 除共同起止点外还要求期间连续，期间缺口返回 null 和原因；
15. disabled/no_data 时不创建缺失 sidecar 目录或文件；
16. 按实际 UTC 时刻选择最新槽（包含等价不同 UTC offset 的时间字符串），最新 partial 优先且不得退回旧 complete；
17. 最新 capture 的 hash 损坏时返回 `invalid_source`，不得退回旧槽；
18. symbol API 暴露 source、5 分钟 bars 和 features，未知 symbol 不得复用其他 symbol；
19. GET overview/symbols 只读 sidecar，前后文件摘要保持一致；
20. late receipt 不得用 captured_at 掩盖旧 cutoff，freshness 需按 cutoff／symbol as-of；
21. breadth 只纳入同一 cutoff 下有完整 session-open 和最新窗口的标的；
22. RS15/RS30 使用 close-to-close 的 4/7 根窗口，并要求窗口连续；
23. contract data schema/provider、scheduled/cutoff、交易日与市场时区 slot、requested symbol scope 均需校验。
24. 用真实 `urus_moomoo.contracts.capture_contract` 与临时 `CollectorStore` 生成 P2 raw/ledger，允许 row 写入时刻与 envelope 采集时刻自然不同；API 仍须按 ledger + envelope 读取 features/source。ledger 使用 fixture-specific task name，防止实现硬编码 `intraday-equities`。
25. quote 保留真实 P2 SDK `update_time`；无时区时间按美东转换为 UTC，缺失或非法时间返回 `quote_time=null`、`quote_time_status=unknown`，不能借用 slot cutoff。

连续性夹具的 RS 边界固定为：在开盘后第 15 分钟制造缺口；最新 4 根 5 分钟 K 仍连续，因此 RS15 可用，最新 7 根包含缺口，因此 RS30 必须不可用。

P2 的 18 项验收和既有采集回归已在 [intraday-equities-test-report-20261015.md](intraday-equities-test-report-20261015.md) 记录为绿灯。P3 API 测试使用临时 raw/scheduled SQLite，覆盖只读连接、按 kind 选择最新槽、ledger task/kind 关联、最新 partial 优先、hash 损坏和禁用/无数据状态；测试不会回退旧 complete 槽或调用 provider。前一轮完整命令与证据：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_intraday_equities_acceptance.py
最终结果：`39 passed`（仅现有 Starlette/httpx deprecation warning）。
```

quote 时间分支的独立命令与结果：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_intraday_equities_acceptance.py::test_projection_quote_uses_sdk_update_time_without_borrowing_slot_cutoff
3 passed（有效、缺失、非法 SDK update_time 分支均通过）
```

与本轮 pure MACD/core 及日线投影相关的回归也已执行：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_macd_momentum_acceptance.py \
  backend/tests/test_macd_projection_acceptance.py \
  backend/tests/test_macd_signal_markers_acceptance.py \
  backend/tests/test_technical.py \
  backend/tests/test_daily_evidence.py \
  backend/tests/test_observation.py
103 passed (仅现有 Starlette/httpx deprecation warning)
```

## Playwright 隔离验收

本轮已准备可复用的 loopback-only fixture 和 Playwright CLI runner，位置为 `/private/tmp/urus-intraday-review-20261015-p3/`：

- `fixture_server.py` 使用固定 A/B/C/D 标的、5 分钟 K、MACD H/ΔH/Δ²H 正负穿越 marker；通过本地 `/__fixture__/state` 切换 `partial`、`stale`、`warmup`、`legacy`、`disabled`、`no_data`、`invalid_source`。
- `start_isolated_review.sh` 只启动本轮自己的 fixture 与临时 Vite 配置，proxy 指向临时 loopback 端口，不读取仓库的 8000 proxy；退出时只结束自己保存的 PID。
- `run_ui_review.sh` 使用现有 Playwright CLI，覆盖 `/intraday` 导航、A/B/C/D 筛选、标的选择、三层 MACD tab、蜡烛图与 MA5、marker/title/时间轴、虚拟时钟自动刷新、状态切换、窄屏溢出、console error 和未匹配 API；`ui_checks.js` 与 `ui_interactions.js` 都在缺失契约时抛错使命令失败。脚本未访问仓库默认的 8000 proxy，也没有连接远程 API。

前端构建命令：

```text
npm --prefix frontend run build
```

结果：`vue-tsc --noEmit` 通过，Vite 转换 79 个模块并成功产出 `frontend/dist`（时间轴锚点修复后复跑）。

隔离 UI 命令：

```text
bash /private/tmp/urus-intraday-review-20261015-p3/start_isolated_review.sh
```

脚本退出码为 0；fixture 和临时 Vite 仅绑定 `127.0.0.1`，退出时已清理本轮自身 PID。Playwright 结果如下：

- `/intraday` 路由、A/B/C/D 角色筛选和 AAA 选择均通过硬断言；A、B、C、D、全部筛选分别只显示预期的 `SPY`、`SMH`、`AAA`、`DDD` 和四个标的。
- 初始页面硬断言通过：3 个 MACD 状态卡、H/ΔH/Δ²H 三个 tab、MACD 图、8 根蜡烛、MA5 overlay、3 个美东时间轴刻度和 H marker 均存在；H marker title 含 `09:50 ET`、`负转正` 和数值。
- 三 tab 切换硬断言通过：H 有 1 个 marker，ΔH 有 2 个 marker，Δ²H 有 2 个 marker；每个 active chart 都带美东时间 aria-label、至少 3 个时间轴刻度，marker title 同时包含 ET 时间和正负穿越标签。蜡烛图 title 含区间时间及 OHLC。
- source identity 硬断言通过：overview 与 `/api/intraday/symbols/SPY` 的 `capture_id` 均为 `fixture-capture-20261015-1410`。
- Playwright Clock `fastForward(60_000)` 硬断言触发了新的 overview 请求，未等待真实 60 秒；fixture 日志共记录 11 个 overview 请求、0 个 `unmatched_api`。
- partial、stale、warmup、legacy、disabled、no_data、invalid_source 7 种 fixture 状态均完成 reload/snapshot，并逐一断言状态属性和可见文案。disabled/no_data/invalid_source 不渲染图表；legacy 明确显示旧投影缺字段并将信号降级为“样本不足”、不渲染 marker。
- 420×900 窄屏 `scrollWidth=420`，无水平溢出；console error 为 0；网络记录只包含 loopback fixture 的预期接口。

关键截图和快照：

- 全页：[intraday-overview.png](../../output/playwright/urus-intraday-review-20261015-p3/intraday-overview.png)
- 蜡烛图与 MA5 局部：[intraday-candle-chart.png](../../output/playwright/urus-intraday-review-20261015-p3/intraday-candle-chart.png)
- 当前 H 图表局部：[intraday-macd-chart.png](../../output/playwright/urus-intraday-review-20261015-p3/intraday-macd-chart.png)
- Δ²H、marker 与时间轴局部：[intraday-macd-delta2.png](../../output/playwright/urus-intraday-review-20261015-p3/intraday-macd-delta2.png)
- 交互后页面：[intraday-macd-markers.png](../../output/playwright/urus-intraday-review-20261015-p3/intraday-macd-markers.png)
- 窄屏：[intraday-narrow.png](../../output/playwright/urus-intraday-review-20261015-p3/intraday-narrow.png)
- DOM 快照：[overview.snapshot.txt](../../output/playwright/urus-intraday-review-20261015-p3/overview.snapshot.txt)、[interaction.snapshot.txt](../../output/playwright/urus-intraday-review-20261015-p3/interaction.snapshot.txt)
