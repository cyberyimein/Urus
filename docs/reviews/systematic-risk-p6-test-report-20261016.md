# P6 系统性风险验收记录（2026-10-16）

## 范围

本轮先验收 P6 CTA 风格代理模型与只读装配接口的红灯契约。纯模型使用独立 SMA、对数收益样本波动率、解析翻转根和敞口分解参考计算；service/API 测试使用既有 `DailyEvidenceRepository` 写入临时 SQLite 的日线、Dataset 和 chart，再用既有 `content_sha256`、`hash_bars`、`compact_bar` 计算身份。测试没有创建系统性风险表、调用 freeze/provider、连接 OpenD、生产 SQLite、SSH、容器或执行部署。UI 验收使用独占的 loopback Vite 进程、固定 Playwright route fixture 和独立 browser session。

测试文件：

- [test_systematic_risk_acceptance.py](../../backend/tests/test_systematic_risk_acceptance.py)
- [test_systematic_risk_service_acceptance.py](../../backend/tests/test_systematic_risk_service_acceptance.py)

## 当前结果

纯模型验收 **24/24 通过**：

- 三组速度窗口 SMA(10,20)、SMA(20,60)、SMA(50,200)；
- 20 个日对数收益、`ddof=1`、`sqrt(252)` 年化波动率及独立敞口分解；
- 等 SMA 但非零波动时趋势信号为 0、敞口为 0；
- 零波动、样本不足、非法 OHLC/收盘价降级；
- 非法窗口、非整数窗口、非正 target volatility/cap 不静默采用默认值；
- 敞口上限、同向多头减仓／空头回补，以及多空跨零的 `long_to_short`／`short_to_long`；
- 负翻转根不可展示为有效价位；非法或非有限假设收盘价不可用；
- 重复同一报价不累计额外日收益，也不改变正式基线。

开发接入 `app.systematic_risk.service.build_overview`、默认关闭配置和只读 API 后，修正一个 fixture 的第二次报价评估时间并补充质量边界，当前 service/API 定向验收为 **26/26 通过**：

```text
backend/.venv/bin/pytest backend/tests/test_systematic_risk_service_acceptance.py -q
..........................                                               [100%]
```

卡片页面隔离 Playwright 验收为 **通过**，覆盖 3 个 ETF 卡片、SMA(10,20)/(20,60)/(50,200) 三种速度、partial 长模型不可用、QFQ/raw 口径不兼容、由多转空、空头回补、四项敞口变化分解的 pp 单位、基线身份、disabled/error、60 秒 GET 刷新和无 `NaN`/`undefined`。请求 route 只允许固定 `/api` GET，未匹配 API、非 loopback 请求、页面异常和非预期 console error 均会失败；fixture 的预期 500 资源错误单独豁免以验证 error 状态显示。

```text
zsh /private/tmp/urus-intraday-p6-review-20261016/run_playwright_p6.sh
status: passed
riskRequests: 4
requestCount: 28
```

截图证据：

- [P6 风险代理卡片](../../output/playwright/urus-intraday-review-20261016-p6/p6-systematic-risk-card.png)
- [SPY 三模型、分解与基线身份](../../output/playwright/urus-intraday-review-20261016-p6/p6-spy-models-and-decomposition.png)

本轮 P5 fixture 的盘中 health 也补齐了 `stale_after_seconds: 600`，供健康状态 UI 显示使用；fixture 仍只提供本地静态数据。

新增覆盖确认：

- 60 根连续真实交易日且 Dataset/chart 质量为 `partial` 时，10/20 与 20/60 模型可用，50/200 明确因样本不足不可用；symbol 与 overview 均为 `partial`。其他 ETF 缺 quote 不屏蔽该结果。
- `conflicted`、`stale`、`missing` 日线质量均拒绝；partial 仍必须通过日期、价格口径、hash 和数据质量校验。
- 先经真实仓储保存，再修改冻结 chart/dataset payload 并重新计算 canonical hash 的非法 OHLC、非有限价格和负成交量均拒绝，区别于单纯 hash 损坏。
- `systematic_risk_enabled=False` 的 GET 返回 disabled，不创建系统性风险表；enabled API 使用固定本地 projection fixture 组装 SPY，调用次数有界，sidecar 路径不存在也不会被创建，Dataset/chart 行数不增加。
- 另以真实 `urus_moomoo.contracts.capture_contract` + `CollectorStore.write_scheduled_raw` 写入临时 sidecar，走未 mock 的 `_load_latest_capture` → `build_intraday_projection` → P6 API：SPY 报价实际解析为 `provider=moomoo_openapi`、`price_basis=raw`、`2026-10-15T14:00:03+00:00`，并与临时 primary SQLite 中的冻结 `NONE` 日线基线成功组装。该桥接测试没有调用 provider/network。
- enabled API 在 primary SQLite 不存在时返回结构化 `unavailable/error`，不创建文件或目录，也不触碰有效 sidecar；损坏的 SQLite 主库返回结构化状态而非 HTTP 500；disabled API 同样不触碰缺失的 primary/sidecar。对应测试用例分别覆盖缺失主库、坏主库和 disabled 只读边界。

## 已落下的行为契约

- 健康 SPY 必须使用恰好前一真实 XNYS session 的日线；缺失该日或历史中间有交易日缺口时，该 ETF 明确不可用，不回退更旧数据伪装当前基线。
- Dataset 和 chart 的 `created_at` 必须不晚于当前报价已知时间；内容 hash 损坏时不可用。
- 报价必须是已知且不超过 600 秒的新鲜 `raw` 价格；`QFQ` 日线与 raw 报价不证明兼容时不可用，未知报价也不可用。
- SPY 与 QQQ 分别评估；D 组或其他 ETF 陈旧不能屏蔽健康 SPY。
- 日线质量允许可验证的 `partial` 进入短模型分层，但不能把长模型样本不足伪装成可用；冲突、陈旧或缺失状态直接降级。
- 冻结日线的 OHLC 必须为有限正数、`high/low` 关系合法，成交量必须为有限非负数；这些检查在 canonical hash 验证之外执行。
- 输出必须带 Dataset ID、chart hash、input hash、最后日线日期、adjustment、稳定 baseline ID；每个 ETF 返回三种速度模型及 provisional scenario。服务测试只断言冻结公共结构，不绑定未冻结的模型名称字段。
- 相同 Dataset 基线下，两个不同报价产生不同 scenario identity；重复组装只读，不累积日收益、不改写 Dataset/chart、不调用 `DailyMarketEvidenceService.freeze`。
- `systematic_risk_enabled` 默认必须为 `False`；关闭 API 应返回明确 disabled 状态，不能创建系统性风险表或读取 provider。
- enabled API 只读取既有 Dataset/chart 和本地 projection；本轮 fixture 明确禁用 Moomoo/FRED/Yahoo，不调用 provider。

## 执行证据

语法检查：

```text
backend/.venv/bin/python -m py_compile \
  backend/tests/test_systematic_risk_acceptance.py \
  backend/tests/test_systematic_risk_service_acceptance.py
```

通过的纯模型命令：

```text
backend/.venv/bin/pytest backend/tests/test_systematic_risk_acceptance.py -q
........................                                                 [100%]
```

最终联合回归（P4 事件 49 项、P5 结构 15 项、P6 纯模型 24 项、P6 service/API 26 项）共 **114/114 通过**：

```text
backend/.venv/bin/pytest \
  backend/tests/test_intraday_events_acceptance.py \
  backend/tests/test_intraday_structure_acceptance.py \
  backend/tests/test_systematic_risk_acceptance.py \
  backend/tests/test_systematic_risk_service_acceptance.py -q
........................................................................ [ 63%]
..........................................                               [100%]
```

前端最终构建通过：

```text
npm --prefix frontend run build
vue-tsc --noEmit && vite build
84 modules transformed
```

本轮 Playwright 只启动并清理独占 loopback Vite 进程，service/API 验收使用本地临时 SQLite 和 FastAPI TestClient；没有连接远程 API、OpenD、生产 SQLite、SSH、容器或执行部署。pytest 仅出现既有 Starlette/httpx deprecation warning。

## 后续复验

P6 后端与 card 页面已通过定向验收。P6 UI fixture 覆盖的是固定本地数据，不代表真实 provider 的报价新鲜度、日线内容或容量；生产启用前仍需在本地真实 sidecar 契约测试中确认这些输入。前端未编写单元测试，也未连接真实 provider。
