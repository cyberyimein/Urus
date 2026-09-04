# SPY/QQQ/IWM 盘中概率预测与蝶式策略数据采集需求书

状态：Proposed
版本：v0.1
日期：2026-09-04
目标：从 2026 年开始积累可审计的 point-in-time 盘中数据，为 2027 年 SPY/QQQ/IWM 收盘分布预测、蒙特卡洛模拟和有限风险蝶式策略 beta 运营提供事实基础。

## 1. 结论与上线定位

三个月数据足以完成以下目标：

- 验证 Moomoo OpenD 盘中采集链路、限频、断线恢复和数据质量；
- 建立第一版收盘条件分布、Monte Carlo 路径生成器和蝶式定价器；
- 进行至少 40 个交易日的 shadow 预测和至少 20 个交易日的模拟交易；
- 发现时间戳、期权链、到期日、报价陈旧、复权和交易日历等工程问题。

三个月数据不足以证明策略拥有跨市场状态的长期收益能力。约 63 个交易日仍主要只有约 63 个独立收盘结果；同一天每五分钟产生的截面高度相关，不能按独立样本计数。2027 年可以启动受限 beta，但不得把三个月结果表述为已经验证的长期稳定收益。

建议的运营分级：

| 阶段 | 最低数据 | 允许行为 |
| --- | ---: | --- |
| Engineering | 20 个完整交易日 | 修复采集、回放、特征与报价问题 |
| Shadow | 40 个完整交易日 | 生成预测和候选蝶式，不提交订单 |
| Paper beta | 60 个完整交易日 | 使用模拟账户或内部虚拟账本 |
| Limited live beta | 60–120 个完整交易日 | 仅人工确认、固定极小风险预算，不宣称长期 edge |
| Production evidence | 至少 250 个交易日，且覆盖多种波动状态 | 才可评价年度稳定性、回撤和模型校准 |

## 2. 项目范围

### 2.1 本阶段必须完成

- 采集 SPY、QQQ、IWM 的一分钟标的行情；
- 每五分钟采集 0DTE 和下一个可交易到期日的期权截面；
- 保存真实 bid/ask、成交、IV、Greeks、成交量和 OI；
- 保存采集时现价、市场时区、交易日、到期日和剩余时间；
- 保存不可变原始事实，并用带版本号的计算生成 Expected Move、skew、DEX/GEX、Gamma Wall、Gamma Profile 和 Max Pain；
- 保证 09:35、10:00、12:30、15:00、15:30、15:50 ET 六个研究锚点优先完成；
- 支持按任意历史截面回放当时模型能够看到的全部输入；
- 为未来 butterfly quote、Monte Carlo forecast 和 paper trade 建立稳定数据契约。

### 2.2 本阶段不包含

- 自动实盘下单；
- LLM 直接预测数值；
- 用当前期权链反推已经过期的历史链；
- 将 OI 直接解释成 dealer 的真实净多空；
- 保存全市场逐笔成交或完整深度盘口；
- 在数据不足时发布经过验证的收益承诺。

## 3. 数据源能力与限制

Moomoo OpenAPI v10.10 可以提供：

- SPY/QQQ/IWM 历史和实时 K 线；
- 当前及未来到期的期权链静态信息；
- 期权 bid/ask、last、volume、OI、IV 和 Greeks；
- 标准 Butterfly 的组合腿查询和组合报价；
- 模拟及实盘组合期权下单。

已知限制：

- `get_option_chain` 不支持查询已过期期权链；
- 美股期权 OI 按交易所节奏每日更新，不是盘中实时持仓变化；
- 美国期权不提供可直接代表 dealer 方向的净 OI；
- 单一期权历史 K 线不能完整还原当时的 bid/ask、全链 IV 曲面和 Greeks；
- 历史期权 K 线额度按期权链和账户级别限制；
- 实时美股期权数据依赖账户地区、OPRA 行情权限和期权权限。

实现和验收应以官方文档为准：

- <https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html>
- <https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html>
- <https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html>
- <https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-strategy.html>
- <https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-quote.html>
- <https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html>

## 4. 采集策略

### 4.1 时间与交易日

- 权威市场时区：`America/New_York`；
- 权威交易日历：当前 Urus 的 `XNYS` 日历；
- 常规交易时段：09:30–16:00 ET，并正确处理节假日和提前收市；
- 标的一分钟 K 线：09:30–16:00 连续采集；
- 期权截面：09:35–15:55 每五分钟采集一次，普通完整交易日约 77 个截面；
- 固定锚点：09:35、10:00、12:30、15:00、15:30、15:50 ET，失败时优先补采；
- 截面时间必须记录 provider 时间、OpenD 接收时间和 Urus 持久化时间。

### 4.2 标的与到期日

第一阶段只允许：

- 标的：`US.SPY`、`US.QQQ`、`US.IWM`；
- 高频报价层：0DTE 和下一个实际存在的交易到期日；
- 结构层：0–7DTE，可在固定锚点低频采集；
- 到期日必须来自 provider 返回值，不通过自然日简单推断；
- 遇到节假日或特殊到期安排时，保存实际 expiration 和交易所状态。

### 4.3 执行价范围

采用双层采集，兼顾蝶式报价和结构完整性。

**Fast band**：

- 现价上下 `±3%`；
- 0DTE 与下一个到期日；
- 每五分钟；
- 用于收盘分布、蝶式枚举、bid/ask 和执行成本建模。

**Structure band**：

- 现价上下 `±10%`，或 provider 返回的完整可用链；
- 0–7DTE；
- 09:35、10:00、12:30、15:00、15:30、15:50；
- 用于 GEX、Gamma Wall、skew、尾部和结构变化研究。

动态范围变化不得覆盖旧截面。每个 capture 保存当时请求范围和实际返回范围。

### 4.4 请求与限频

- 所有请求继续复用 `MoomooCollectionCoordinator` 的主机级锁和 rate class；
- 单次 market snapshot 最多放入 400 个合约；
- 按 symbol、expiration、band 分批，不能依赖恰好少于 400；
- 对同一个 capture 去重 option chain 和 metadata 请求；
- 本模块的 SPY、QQQ、IWM 优先于 Urus 普通观察池取得历史 K 线标的额度；先满足三者，再由普通历史容量规划器对剩余额度应用安全预留；
- 若历史 K 线剩余额度为零，缺失标的的分钟线 fail-closed，但三者的期权截面继续采集；
- 截面调度优先保证固定锚点，其次保证普通五分钟截面，再次保证宽范围结构层；
- 达到额度、行情权限丢失或 OpenD 不可用时 fail-closed，保存明确状态，不用旧数据冒充当前截面；
- 不因补采而伪造原始采集时间。晚到数据必须标记 `late_capture=true`。

## 5. 原始数据契约

### 5.1 Capture Run

每次调度形成一个不可变 `IntradayOptionCaptureRun`：

```text
id
schema_version
trading_date
scheduled_at
captured_at
provider_time_min
provider_time_max
market_session
symbols_requested
symbols_completed
expirations_requested
request_band_percent
capture_tier
status
is_late_capture
warnings
content_sha256
created_at
```

唯一性建议：

```text
(trading_date, scheduled_at, capture_tier, schema_version)
```

重试应复用同一逻辑 capture，不生成两份相互竞争的事实；provider 返回内容不同则保存 attempt 审计记录。

### 5.2 标的一分钟行情

```text
symbol
bar_time
open
high
low
close
volume
turnover
vwap_or_null
session
provider
received_at
quality_state
```

主键建议：`(symbol, bar_time, provider)`。

### 5.3 期权合约快照

```text
capture_run_id
underlying_symbol
underlying_spot
contract_code
option_type
expiration
days_to_expiry
strike
contract_multiplier
exercise_type
bid
ask
bid_size
ask_size
last
mark_or_mid
volume
open_interest
implied_volatility
delta
gamma
vega
theta
rho
quote_time
received_at
quote_age_ms
data_state
warnings
```

要求：

- `mid` 只能由有效 `bid <= ask` 计算；
- bid/ask 缺失、交叉、为零或陈旧时保留原值并打质量标记；
- OI 必须标注其为最近一次交易所日更新数据，不得声称为盘中新增持仓；
- provider Greeks 和自算 Greeks 不得写入同一个无来源字段；
- 每个派生字段都要能够追溯到 capture 和 calculation version。

### 5.4 链元数据

合约代码、到期日、执行价、类型、乘数、行权方式等静态字段应去重存储，并保留首次和最后观察时间。不得依赖未来重新查询已到期链。

## 6. 派生数据与版本

原始快照入库后异步或同进程生成 `IntradayOptionFeatureSnapshot`：

```text
capture_run_id
calculation_version
spot
time_to_close_seconds
atm_strike
atm_call_mid
atm_put_mid
expected_move_amount
expected_move_percent
iv_atm
iv_skew_25d
iv_term_slope
call_dex
put_dex
net_dex
call_gex
put_gex
modeled_net_gex
gamma_flip_levels
call_wall
put_wall
max_pain
realized_vol_intraday
distance_to_vwap
range_consumed_ratio
quality_state
warnings
```

DEX/GEX 必须保留模型假设。至少同时支持：

- 当前 Urus canonical 假设；
- dealer-position neutral/sensitivity 场景；
- 后续校准版本。

修改算法只能创建新的 `calculation_version`，不得覆盖旧计算。

## 7. 监督标签

每个历史截面在官方 regular close 可用后追加不可变 outcome：

```text
capture_run_id
official_close
close_source
close_return_from_spot
absolute_close_move
close_percentile_in_implied_distribution
minutes_to_close
realized_max_up_move
realized_max_down_move
completed_at
```

训练和评估必须遵守：

- 训练/验证/测试按完整交易日切分；
- 同一交易日所有截面只能出现在同一个 split；
- 使用 walk-forward，不允许随机打散盘中行；
- 预测 12:30 收盘时只能使用 12:30 及之前的事实；
- 当天最终 OI、收盘后修正行情或未来到期信息不得泄漏到输入；
- 每个模型运行保存 dataset hash、feature version、model version 和 cutoff time。

## 8. 蝶式研究数据

从 Fast band 枚举同到期、等翼宽的 call butterfly 和 put butterfly：

```text
long 1 x (K - width)
short 2 x K
long 1 x (K + width)
```

候选范围：

- 中心 `K` 位于预测分布第 20–80 百分位或配置范围内；
- 翼宽按有效执行价间隔枚举；
- 三腿必须同时有新鲜双边报价；
- 保存逐腿 NBBO、组合自然价、组合中间价和 Moomoo combo quote；
- 回测默认使用保守可执行价格，不使用无条件 mid 成交；
- 策略成本包含佣金、监管费用、滑点和未成交概率模型。

每个候选保存：

```text
capture_run_id
expiration
center_strike
wing_width
legs
natural_debit
mid_debit
combo_quote
max_profit
max_loss
lower_breakeven
upper_breakeven
quote_quality
```

第一阶段只记录候选，不自动提交订单。

## 9. 数据量估算

### 9.1 基准假设

- 2 个标的；
- 2 个高频到期日；
- 每个到期日平均 40–60 个执行价；
- 每个执行价 call/put 两行；
- 每日 77 个五分钟截面；
- 每年约 252 个交易日。

期权快照行数：

| 情景 | 每截面合约数 | 三个月约 63 日 | 一年约 252 日 |
| --- | ---: | ---: | ---: |
| 40 strikes/expiry | 320 | 155 万 | 621 万 |
| 50 strikes/expiry | 400 | 194 万 | 776 万 |
| 60 strikes/expiry | 480 | 233 万 | 931 万 |

### 9.2 磁盘估算

采用规范化数值列、必要索引、按日期分区或等价方案时：

| 内容 | 三个月 | 一年 |
| --- | ---: | ---: |
| 规范化期权快照数据 | 1–3 GB | 4–10 GB |
| 索引、数据库页膨胀和维护余量 | 2–5 GB | 8–20 GB |
| 压缩原始响应和 Parquet 归档 | 1–4 GB | 3–12 GB |
| 日志、WAL、临时空间和一份备份 | 2–5 GB | 8–20 GB |
| 推荐规划容量 | **5–15 GB** | **20–50 GB** |

以上不包含完整逐笔成交和多档订单簿。若改为每分钟保存全链或盘口深度，年度容量可能快速增至 100–300 GB 以上。

### 9.3 NAS 决策

MVP 不要求 NAS：

- Mac mini 本地 SSD 为主存储；
- 为数据库、WAL、临时计算和增长预留至少 100 GB 可用空间；
- 主数据库不放在普通 NAS 网络卷上，避免网络中断和随机 I/O 延迟影响采集；
- 每日收市后将完成日期分区导出为 Parquet + Zstandard；
- 如已有 NAS，将其作为冷归档和备份目标；
- 没有 NAS 时，可先使用另一块本地/外接 SSD 或兼容对象存储作为第二份副本。

满足任一条件时再把 NAS 列为必需基础设施：

- 扩展到 10 个以上标的；
- 改为 1 分钟全链；
- 保存多档 order book 或 tick 数据；
- 保留多份不可变原始 payload；
- 本地可用空间低于未来 12 个月预计用量的三倍。

## 10. 保留与备份策略

- 规范化原始事实：长期保留；
- 原始 provider payload：热存 90 天，之后压缩归档；
- 派生特征：长期保留并携带 calculation version；
- debug 日志：默认 30 天；
- 数据库每日备份，至少保留 14 个日备份、8 个周备份和 12 个按月归档；
- 每日归档生成 row count、min/max timestamp 和 SHA-256 manifest；
- 每月至少执行一次恢复演练，不能只验证“备份文件存在”。

## 11. 数据质量与监控

每个交易日输出质量报告：

- 预期 capture 数、成功数、迟到数和缺失数；
- 每个 symbol/expiration 的返回合约数量；
- bid/ask 缺失率、交叉率、零报价率和陈旧率；
- IV/Greeks 缺失率；
- provider 时间与接收时间延迟；
- 0DTE 到期日识别是否正确；
- SPY/QQQ/IWM 一分钟 K 线缺口；
- OI 更新时间和连续性；
- 原始 capture 与派生特征 hash 是否一致；
- OpenD 断线、行情权限和 quota 状态。

建议告警阈值：

- 固定锚点任一缺失：立即告警；
- 普通截面日完整率低于 95%：当日降级；
- quote age 超过 30 秒：该合约不可用于可执行蝶式回测；
- 有效双边报价覆盖低于 80%：该截面不可用于策略评分；
- official close 缺失：不得生成监督标签；
- 时钟偏差超过 2 秒：停止形成正式 capture，保留失败记录。

## 12. 三个月验收标准

### 12.1 采集验收

- 至少 60 个完整交易日；
- 日级采集完整率中位数不低于 98%；
- 六个固定锚点完整率不低于 99%；
- 所有数据可按 capture ID、时间、symbol、expiration 重放；
- 断线重连、OpenD 重启和进程重启不会生成重复逻辑截面；
- 已过期合约仍可从本地链元数据和快照中完整读取；
- 随机抽取 20 个截面，与当日原始 payload 对账一致。

### 12.2 模型准备验收

- 能为每个固定锚点生成收盘预测分布；
- 输出 5%、16%、50%、84%、95% 分位；
- 与 current-price、historical-vol 和 ATM implied-move 基准比较；
- 报告 CRPS、pinball loss、区间覆盖率和区间宽度；
- 按交易日 walk-forward，且自动检测同日泄漏；
- Monte Carlo 随机种子、参数和模型版本可复现。

### 12.3 蝶式准备验收

- 可以使用历史截面重新生成当时可见的候选蝶式；
- 回测使用当时 bid/ask 或组合报价，不使用未来或收盘 mid；
- 输出毛收益、净收益、未成交率、最大回撤和尾部亏损；
- SPY、QQQ 与 IWM 同方向仓位按同一风险簇处理；
- paper 账本包含所有打开但尚未关闭的头寸，不只统计已平仓赢家。

## 13. 实施顺序

### Phase A：采集基础（第 1–2 周）

- 新增盘中 capture run、标的一分钟 K 线和期权快照模型；
- 在现有 coordinator 中增加盘中 rate classes；
- 增加 5 分钟调度、固定锚点优先级、断线恢复和质量状态；
- 完成 SPY 单标的 shadow 采集，再加入 QQQ 和 IWM；三者优先取得历史 K 线标的额度。

### Phase B：质量与归档（第 3–4 周）

- 增加日级质量报告；
- 增加 Parquet/Zstandard 日分区归档与 manifest；
- 增加备份恢复测试；
- 连续 10 个交易日无关键截面缺失后进入下一阶段。

### Phase C：分布基准（第 2 个月）

- current-price、历史条件分布和 implied-move 三个基准；
- 固定 10:00、12:30 和 15:00 三个预测点；
- 建立 walk-forward 评估；
- 暂不使用复杂神经网络。

### Phase D：Monte Carlo 与蝶式 shadow（第 3 个月）

- 用历史条件残差或参数模型生成路径；
- 加入 gamma regime 的情景敏感性，而非把单一 GEX 当事实；
- 枚举蝶式并记录当时可执行报价；
- 建立 paper 账本、持仓和最大风险预算；
- 三个月结束时只做 go/no-go 评审，不自动转实盘。

## 14. Go/No-Go 原则

允许进入 2027 limited beta 的必要条件：

- 采集、重放、时间边界和备份全部通过验收；
- 至少 40 个交易日 shadow + 20 个交易日 paper；
- 模型相对三个朴素基准至少在分布评分或扣费后经济指标上稳定改善；
- 改变单个最佳交易日或最佳蝶式后，整体结论不翻转；
- 所有未平仓仓位纳入净值和风险；
- 人工确认和 kill switch 可用；
- 上线文案明确说明样本有限、处于 beta，不承诺收益。

以下任一条件触发 No-Go：

- 数据泄漏、时间戳不可信或无法重放；
- 结果依赖 mid 成交或忽略组合滑点；
- 收益主要由一两个交易日贡献；
- 固定锚点缺失率超过 1%；
- 模型只改善点预测，但蝶式扣费后期望值没有改善；
- 无法给出组合级最大回撤和最大同时风险。

## 15. 与当前 Urus 边界的关系

当前 Urus 的日线 Observation Run 和收市期权证据继续保持不变。盘中采集应新增独立事实类型，但必须复用现有 provider adapter、主机锁、限频、状态语义、不可变 hash 和版本规则。

建议边界：

```text
Moomoo OpenD
    -> Intraday Collection Coordinator
    -> Immutable Capture Runs
    -> Raw Underlying/Option Facts
    -> Versioned Feature Snapshots
    -> Forecast Dataset
    -> Monte Carlo / Butterfly Research
    -> Shadow or Paper Ledger
```

在本需求书完成前，Urus 仍不下单；未来自动交易应作为单独 ADR 和安全评审事项处理。
