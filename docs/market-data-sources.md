# 阶段 1A / 3A 数据来源矩阵

本文件把当前已经确认的“两次执行策略”数据边界写成可验收记录。每个来源保留来源、时间和质量状态；无法取得的数据标记为 `unavailable`/`partial`，不会填 0、旧值或 mock。

## 当前策略边界

- 每次运行通过一次 Moomoo `get_market_snapshot` 批量采集大盘代理和跨资产 ETF；不建立实时订阅，不采集逐笔和完整盘口。
- 当前自动批量集合：`QQQ, SPY, IWM, DIA, RSP, SMH, SOXX, IGV, HYG, LQD, TLT, IEF, UUP, GLD, USO`。
- VIX 直接指数不通过 Moomoo 请求；每次运行都请求 Yahoo `^VIX`，Yahoo 值优先，FRED VIX 保留作交叉校验。
- 宏观日频数据优先使用 Yahoo 可用的 `^VIX/^TNX/^TYX`；FRED 提供官方 2Y 常期限收益率，并保留 10Y/30Y/VIX 交叉值，2s10s 使用选定的 10Y 与 FRED 2Y 计算。
- QQQ 已返回的 OHLCV 日线直接计算 20 日年化实现波动率、ATR14、ATR14%、布林带 20/1、20/2、20/3、带宽、MACD(12,26,9) 和 Effort vs Result；不增加 Moomoo 实时订阅请求。
- 3A 默认使用全量 `INSTRUMENT_VALIDATION_SYMBOLS`（SPY、SMH、IGV 与 15 个公开关注股），自动加入 QQQ 作为相对强弱基准。一次 `get_market_snapshot` 批量读取全量标的，再按标的请求复权日线；不建立实时订阅。日线直接计算 1/5/20/60/120/252 日收益、MA10/20/50/100/200、实现波动率、ATR14、多轨布林、MACD、量价信号和相对 QQQ 收益、Beta、相关性。
- 3A 在采集前后读取 Moomoo 订阅状态和历史 K 线额度，记录 `subscription_unchanged`、`history_used_delta` 和告警；额度检查本身不发起订阅。
- 快照同时保存正规交易价与 `pre_price`/`after_price`；前端分别展示常规价、盘前价和盘后价。夜间休市时不会把盘后价伪装成正规收盘价。
- 期货、实时订阅、逐笔、完整盘口不在本轮范围；5 分钟历史和 5 年日线历史不由快照提供，只有策略指标明确需要时再单独接入。

## 实际验证记录（2026-08-03）

通过部署环境提供的 `<opend-host>:11111` OpenD 做了只读验证：

| 数据组 | 请求/来源 | 结果 | 记录 |
|---|---|---|---|
| 大盘/跨资产 ETF | 一次 Moomoo snapshot 批量请求 | 已验证 | QQQ、SPY、IWM、DIA、RSP、SMH、SOXX、IGV、HYG、LQD、TLT、IEF、UUP、GLD、USO 全部返回快照 |
| VIX 直接指数 | Moomoo OpenD | 按策略跳过 | OpenD 不支持美股指数；不再发送 `US..VIX` 请求，不能把 VIXY 当作 VIX |
| VIX 日值 | Yahoo `^VIX`，FRED `VIXCLS` 交叉 | 已验证 | Yahoo 每次运行请求并作为选定值，FRED 保留交叉值 |
| 2Y Treasury | FRED `DGS2` | 已验证 | Yahoo chart 不提供可靠的官方 2Y 常期限系列，FRED 为选定来源 |
| 10Y/30Y 与 2s10s | Yahoo `^TNX/^TYX` + FRED `DGS10/DGS30` 交叉 | 已验证 | Yahoo 10Y/30Y 为选定值；2s10s 由 Yahoo 10Y - FRED 2Y 机械计算 |
| Anomalo 旧工具 | 旧版代码检视 | 未复用 | 没有可直接复用的非 Moomoo 行情 provider |
| 3A 个股/行业 ETF | 一次 Moomoo snapshot + QQQ 基准及全量标的复权日线 | 已落地 | 由 `INSTRUMENT_VALIDATION_SYMBOLS` 控制全量集合；相对强弱和技术指标质量按标的记录，不改变实时订阅状态 |

## 当前 read model 结构

- `market.market_snapshot.quotes`：批量返回的 ETF 快照。
- `market.market_snapshot.unavailable_symbols`：批量请求没有返回的标的。
- `market.market_snapshot.vix`：Moomoo 直接 VIX 的策略状态；当前为 `status=skipped`，不代表采集失败。
- `market.macro_context.observations`：选中的日频宏观观测，VIX/10Y/30Y 优先来自 Yahoo，2Y 来自 FRED。
- `market.macro_context.cross_checks`：另一来源的交叉值，例如 `vix_fred`、`us_10y_yield_fred`。
- `market.macro_context.yahoo`：Yahoo 是否实际请求、优先指标是否返回和来源质量。
- `market.history.technical_indicators`：QQQ 技术指标及各项 `as_of`、`sample_count`、`source`。
- `market.history.technical_indicators.macd_12_26_9`：收盘日线 MACD 的 DIF、DEA、柱体、交叉和动量状态。
- `market.history.technical_indicators.volume_effort_result`：成交量相对 20 日均量、真实波幅、收盘位置和放量/缩量信号；`combination` 保留放量/正常量/缩量 × 上涨/下跌/横盘的完整 3×3 组合，前端会将原来归并为中性的组合明确显示；缺成交量时为 `unavailable`。
- `market.history.technical_indicators.rsi_context`：基于 Wilder RSI14、此前 20/60 日高低点、均线、MACD、量价和 ATR 生成延续/反转评分，区分 `breakout_confirmed`、`exhaustion_watch`、`breakdown_confirmed`、`reversal_watch` 等状态。只提供研究上下文，不直接代表买入或卖出。
- `instrument_cards`：3A QQQ 基准、核心 ETF 和公开关注股的快照、日线收益、技术指标、主题分组和相对 QQQ 强弱；每张卡显式保留 `provider`、`source_mode`、`captured_at`。
- `instrument.quota_audit`：3A 采集前后订阅/历史额度快照及变化量。
- 所有宏观观测保留 `as_of`；Yahoo 数据不会伪装成实时行情。

## 延期项（不阻塞本次 1A 手动验收）

1. Moomoo OpenD 当前不能通过 `get_market_snapshot` 返回直接 VIX 指数；按策略不再请求该指数。Yahoo `^VIX` 已设置为每次运行必取并优先使用，FRED VIX 作为交叉校验，Yahoo 不能替代官方 2Y 数据。
2. 5 年日线原始归档、5 分钟 OHLCV 和复播仍未实现。
3. 市场全体涨跌家数、完整市场宽度和成分热力图不是少量 ETF 快照，仍需单独的市场广度来源。
4. 5 年日线原始归档、分钟级 OHLCV 和更丰富的行业基准仍延期；3A 当前只保存本轮请求返回的日线窗口。MACD、布林和量价信号复用这批已保存日线，不额外占用实时订阅额度。
5. 调度器已接入 `exchange-calendars` 的 `XNYS` 交易日历，自动过滤休市日，并依据实际收盘时间调整提前收盘日的尾盘与盘后槽位。
6. 期货、订阅、逐笔、盘口和事件/新闻层按当前两次执行策略暂不实现。

## 阶段 1A 当前验收状态

- Moomoo 批量代理快照：已完成并通过真实 OpenD 验证。
- FRED 日频宏观：已完成并通过真实请求验证。
- Yahoo 每次运行的 VIX/收益率采集：已完成并通过真实 Yahoo chart 请求验证。
- VIX 直接 Moomoo 快照：受 OpenD/权限限制按策略跳过；Yahoo 每次运行必取并优先使用，FRED 保留交叉校验。
- 1A 手动验收范围：真实 ETF 批量快照、Yahoo/FRED 宏观、QQQ 日线指标、UTC 时间和状态语义均已完成；延期项不再阻塞本次签收。
- 当前真实运行的整体状态为 `mixed`：1A、3A 为 `succeeded + live`，1B/3B 为 `skipped`，2/4 为 `placeholder`；整体为 `mixed` 是因为后续步骤仍未实现，不代表 3A 失败。

## 阶段 3A 技术验收

- 已通过真实 OpenD 验证 `QQQ + INTC + SMH` 的同批快照和日线采集；全量落地后由同一批处理路径覆盖配置的核心 ETF 与公开关注股，技术指标质量按卡片记录。
- 以 QQQ 为基准，3A 同时输出 5/20/60 日绝对收益、相对收益、20/60 日 Beta 与相关性；前端“个股与行业 ETF”页按主题 Tab 显示每组 live 数量和摘要表，完整字段按标的折叠展开。
- SQLite migration `0003_instrument_technical_persistence` 保存分析批次、标的快照和逐日 OHLCV/成交数据；与 frontend snapshot 在同一事务写入。
