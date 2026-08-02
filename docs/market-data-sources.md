# 阶段 1A 数据来源矩阵

本文件把当前已经确认的“两次执行策略”数据边界写成可验收记录。每个来源保留来源、时间和质量状态；无法取得的数据标记为 `unavailable`/`partial`，不会填 0、旧值或 mock。

## 当前策略边界

- 每次运行通过一次 Moomoo `get_market_snapshot` 批量采集大盘代理和跨资产 ETF；不建立实时订阅，不采集逐笔和完整盘口。
- 当前自动批量集合：`QQQ, SPY, IWM, DIA, RSP, SMH, SOXX, IGV, HYG, LQD, TLT, IEF, UUP, GLD, USO`。
- VIX 直接指数不通过 Moomoo 请求；每次运行都请求 Yahoo `^VIX`，Yahoo 值优先，FRED VIX 保留作交叉校验。
- 宏观日频数据优先使用 Yahoo 可用的 `^VIX/^TNX/^TYX`；FRED 提供官方 2Y 常期限收益率，并保留 10Y/30Y/VIX 交叉值，2s10s 使用选定的 10Y 与 FRED 2Y 计算。
- QQQ 已返回的 OHLC 日线直接计算 20 日年化实现波动率、ATR14、ATR14%、布林带 20/2；不增加 Moomoo 请求。
- 期货、实时订阅、逐笔、完整盘口不在本轮范围；5 分钟历史和 5 年日线历史不由快照提供，只有策略指标明确需要时再单独接入。

## 实际验证记录（2026-08-03）

通过 `opend-host:11111` 的 OpenD 做了只读验证：

| 数据组 | 请求/来源 | 结果 | 记录 |
|---|---|---|---|
| 大盘/跨资产 ETF | 一次 Moomoo snapshot 批量请求 | 已验证 | QQQ、SPY、IWM、DIA、RSP、SMH、SOXX、IGV、HYG、LQD、TLT、IEF、UUP、GLD、USO 全部返回快照 |
| VIX 直接指数 | Moomoo OpenD | 按策略跳过 | OpenD 不支持美股指数；不再发送 `US..VIX` 请求，不能把 VIXY 当作 VIX |
| VIX 日值 | Yahoo `^VIX`，FRED `VIXCLS` 交叉 | 已验证 | Yahoo 每次运行请求并作为选定值，FRED 保留交叉值 |
| 2Y Treasury | FRED `DGS2` | 已验证 | Yahoo chart 不提供可靠的官方 2Y 常期限系列，FRED 为选定来源 |
| 10Y/30Y 与 2s10s | Yahoo `^TNX/^TYX` + FRED `DGS10/DGS30` 交叉 | 已验证 | Yahoo 10Y/30Y 为选定值；2s10s 由 Yahoo 10Y - FRED 2Y 机械计算 |
| Anomalo 旧工具 | 旧版代码检视 | 未复用 | 没有可直接复用的非 Moomoo 行情 provider |

## 当前 read model 结构

- `market.market_snapshot.quotes`：批量返回的 ETF 快照。
- `market.market_snapshot.unavailable_symbols`：批量请求没有返回的标的。
- `market.market_snapshot.vix`：Moomoo 直接 VIX 的策略状态；当前为 `status=skipped`，不代表采集失败。
- `market.macro_context.observations`：选中的日频宏观观测，VIX/10Y/30Y 优先来自 Yahoo，2Y 来自 FRED。
- `market.macro_context.cross_checks`：另一来源的交叉值，例如 `vix_fred`、`us_10y_yield_fred`。
- `market.macro_context.yahoo`：Yahoo 是否实际请求、优先指标是否返回和来源质量。
- `market.history.technical_indicators`：QQQ 技术指标及各项 `as_of`、`sample_count`、`source`。
- 所有宏观观测保留 `as_of`；Yahoo 数据不会伪装成实时行情。

## 延期项（不阻塞本次 1A 手动验收）

1. Moomoo OpenD 当前不能通过 `get_market_snapshot` 返回直接 VIX 指数；按策略不再请求该指数。Yahoo `^VIX` 已设置为每次运行必取并优先使用，FRED VIX 作为交叉校验，Yahoo 不能替代官方 2Y 数据。
2. 5 年日线原始归档、5 分钟 OHLCV 和复播仍未实现。
3. 市场全体涨跌家数、完整市场宽度和成分热力图不是少量 ETF 快照，仍需单独的市场广度来源。
4. 60/120/252 日收益延期；相对强弱放到 3A，计算个股相对 QQQ、行业 ETF 相对 QQQ。
5. 交易日历与提前收盘不阻塞本次手动验收，但在启用每天两次自动调度前必须实现。
6. 期货、订阅、逐笔、盘口和事件/新闻层按当前两次执行策略暂不实现。

## 阶段 1A 当前验收状态

- Moomoo 批量代理快照：已完成并通过真实 OpenD 验证。
- FRED 日频宏观：已完成并通过真实请求验证。
- Yahoo 每次运行的 VIX/收益率采集：已完成并通过真实 Yahoo chart 请求验证。
- VIX 直接 Moomoo 快照：受 OpenD/权限限制按策略跳过；Yahoo 每次运行必取并优先使用，FRED 保留交叉校验。
- 1A 手动验收范围：真实 ETF 批量快照、Yahoo/FRED 宏观、QQQ 日线指标、UTC 时间和状态语义均已完成；延期项不再阻塞本次签收。
- 当前真实运行的整体状态为 `mixed`：1A 为 `succeeded + live`，1B/3B 为 `skipped`，2/4 为 `placeholder`，3A 为 `unavailable`。
