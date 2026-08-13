# CTA 分支 AI 决策与 IV/HV 需求

> 决策日期：2026-08-13  
> 状态：产品与研发需求已确认，代码尚未按本文全部实现。  
> 范围：CTA 研究变体、Stage 4 AI 调度、期权波动率上下文。本文不授权自动交易执行。

## 1. 已确认的产品决策

1. 每个美东交易日只运行两次 AI：盘前决策和收盘后复盘。
2. 尾盘阶段只采集、校验、冻结和持久化数据，不调用 AI，也不产生尾盘预测。
3. CTA 正式模型以完整日线为主，收盘后更新一次正式仓位；盘前 AI 继承最近一次正式 CTA 状态，
   加入隔夜行情、事件和期权变化进行解释。
4. 盘中或尾盘 CTA 结果如需展示，只能标为 `intraday_estimate`；不能覆盖基于完整日线的
   `official_close_model`。
5. AI 不重算 CTA 仓位、波动率目标、调仓压力或触发价，也不把 ETF 代理估算描述为真实 CTA
   持仓或真实资金流。
6. 期权上下文增加 IV/HV 背离，但它是波动率定价信号，不是方向信号，也不能单独触发买入跨式、
   卖出波动率或股票方向交易。

## 2. 每日运行时序

| 阶段 | 数据动作 | CTA 动作 | AI 动作 | 正式产物 |
| --- | --- | --- | --- | --- |
| `pre_market` | 采集盘前行情、期权、事件和质量信息 | 读取最近一次 `official_close_model`；可生成单独的盘前估算 | 运行盘前决策 | `forecast`、风险与触发条件 |
| `pre_close` | 采集尾盘行情、期权链、事件结果和质量信息并冻结 | 可生成 `intraday_estimate`，不得晋升为正式仓位 | **不调用 AI** | 尾盘 Observation、Technical Report、质量审计 |
| `post_close_review` | 采集官方收盘、完整日线和可用期权快照 | 计算新的 `official_close_model` | 运行复盘 | 盘前预测评分、当日解释、下一交易日基线 |

调度器可以继续保留三个 `run_type`，但 `pre_close` 必须具备确定性的
`decision_policy=collection_only`。它不创建模型调用；若为审计需要创建 Decision Session，则状态应为
`not_applicable` 或 `skipped_by_policy`，不能伪装成失败或缺失决策。

### 2.1 阶段血缘

- 盘前 AI 只能读取当前盘前快照、最近一个可用交易日的收盘复盘和正式 CTA 状态。
- 收盘复盘读取同日盘前决策、同日尾盘冻结数据和官方收盘数据。
- 尾盘数据属于复盘证据，不属于一次独立预测。
- 所有父报告查询继续受 `cutoff_time` 限制，禁止回放时读取未来报告。
- 收盘评分只评价盘前预测；原有 `pre_close_evaluation` 迁移为可空兼容字段，后续 Schema 应删除。

### 2.2 CTA 计算频率

- `official_close_model`：每个有效交易日收盘后最多生成一个版本。
- `pre_market_context`：不重新解释未完成日线为正式信号，只记录隔夜偏移和事件风险。
- `intraday_estimate`：可选，仅用于观察“若按当前价格收盘，模型可能如何变化”；必须携带
  `provisional=true`、观测时间和使用的未完成价格。
- 没有新正式模型时返回 `unchanged_since`，避免 AI 把相同仓位包装成新决策。

## 3. AI 与确定性程序的边界

确定性程序负责：

- CTA 原始信号、目标暴露、上一目标暴露、机械动作和压力指数；
- 资产类别聚合、gross/net exposure、相关性和风险贡献；
- IV/HV 派生值、期限匹配、历史分位、事件标记和数据质量；
- 实际收益、方向命中、Brier 分数和其他复盘评分；
- 数据新鲜度、最小历史长度和是否允许进入 AI 的 Gate。

AI 负责：

- 解释存量 CTA 仓位和边际调仓压力；
- 识别跨资产、事件、期权和趋势证据之间的支持或冲突；
- 说明 IV/HV 背离更可能来自波动率折价、一次性已实现跳空还是事件窗口；
- 输出可证伪的观察条件和风险提示；
- 引用冻结数据路径，不自行补数或做自然语言算术。

V1 不允许 AI 修改程序给出的 CTA 目标仓位。若未来需要 AI 例外处理，只能先输出结构化
`human_review_flag`，不能直接进入订单执行。

## 4. IV/HV 当前数据盘点

### 4.1 已有数据

Moomoo 期权采集目前已经保存：

- 标的综合 IV；
- 每份期权合约的 implied volatility；
- IV Rank；
- IV Percentile；
- HV30（现有原始字段名为 `hv_30d`）。

相关实现：

- `backend/app/integrations/moomoo_options.py` 将 `iv`、`iv_rank`、`iv_percentile`、`hv_30d`
  写入 symbol overview，并保存每份合约的 `implied_volatility`。
- `option_symbol_snapshots.overview` 保留 symbol overview。
- `option_contract_snapshots.implied_volatility` 保留合约 IV。
- `get_option_overview` 会向 Agent 返回完整 overview，因此当前 AI 理论上能读取 HV30。

### 4.2 已验证的真实样本

本地 `backend/urus.db` 的 `option_symbol_snapshots` 已验证存在 2026-08-04 20:32:24 的真实
Moomoo 快照：

| 标的 | IV | HV30 | IV−HV30（百分点） | IV/HV30 |
| --- | ---: | ---: | ---: | ---: |
| AMZN | 35.429% | 58.328% | -22.899 | 0.607 |
| MSFT | 32.843% | 55.544% | -22.701 | 0.591 |
| GOOG | 34.938% | 46.601% | -11.663 | 0.750 |
| AAPL | 28.475% | 36.672% | -8.197 | 0.777 |
| QQQ | 25.165% | 26.230% | -1.065 | 0.959 |

这些值证明第一版可以直接从现有快照计算 IV/HV 信号。它们不证明该信号已经具有交易收益，
也不能替代历史回测。

### 4.3 当前缺口

- 前端只展示 IV、IV Rank 和 IV Percentile，尚未展示 HV30、spread、ratio 或风险溢价分类。
- 系统尚未生成 `iv_hv_spread`、`iv_hv_ratio` 和历史 percentile。
- 尚无稳定的“30D ATM IV 日历史序列”和期限插值结果。
- 尚未计算 HV30 的 10/20/60 日趋势。
- AI Prompt 未明确要求检查 IV/HV 背离、期限匹配和事件污染。
- `compare_option_observations` 当前先取 expiration 结构，再从其中读取不存在的 `spot` 和
  `overview`，导致 IV/IV Rank 比较路径返回空值；它也没有比较 HV30。
- 当前比较工具默认围绕旧的盘前/尾盘配对。两次 AI 调度落地后，需要允许显式选择
  `from_phase` 和 `to_phase`，以支持昨日收盘→今日盘前、今日盘前→尾盘、盘前→正式收盘。

## 5. IV/HV 派生指标定义

所有输入在计算前统一为年化百分比数值；必须在 Schema 中明确 `unit=percent`。spread 的单位是
百分点，不是百分比变化：

```text
iv_hv_spread = matched_term_iv - hv_30d
iv_hv_ratio = matched_term_iv / hv_30d
volatility_risk_premium_proxy = iv_hv_spread
```

当第一版只有 Moomoo 综合 IV 时：

```text
matched_term_iv = overview.iv
term_match_method = provider_composite_proxy
model_fidelity = proxy
```

HV30 为空、非正数或时间戳过期时，ratio 和分类必须为 `null/unknown`，禁止除零或用其他标的补齐。

### 5.1 第一版分类阈值

以下是可配置的研究初值，不是已经验证的交易阈值：

| IV/HV30 | 分类 |
| ---: | --- |
| `< 0.70` | `deep_discount` / 显著折价 |
| `>= 0.70` 且 `< 0.90` | `moderate_discount` / 温和折价 |
| `>= 0.90` 且 `< 1.10` | `matched` / 基本匹配 |
| `>= 1.10` 且 `< 1.40` | `moderate_premium` / 温和溢价 |
| `>= 1.40` | `large_premium` / 显著溢价 |

边界只有一个归属，按上表实现为左闭右开区间；`1.40` 归入 `large_premium`。

### 5.2 历史标准化

正式版本使用标的自身历史分布：

```text
iv_hv_percentile = percentile_rank(current iv_hv_ratio,
                                   symbol's point-in-time ratio history)
```

- 最低 60 个有效交易日才提供 provisional percentile。
- 达到 252 个有效交易日后标为 mature。
- 不允许用当前时点之后的历史值，防止 look-ahead bias。
- 需要保存原始输入、计算版本、样本数、窗口起止和缺失比例。

### 5.3 匹配期限的 ATM IV

正式目标是约 30 日 ATM IV，而不是笼统综合 IV：

1. 每个到期日从可交易合约中选择最接近 ATM 的 call/put，优先使用有效 bid/ask 中间价对应的 IV；
2. 排除 crossed/locked、极宽价差、无有效报价和异常 IV 合约；
3. 对 30 日两侧最近的有效到期日按总方差进行期限插值；
4. 只有单侧期限时允许降级为 nearest-term proxy，并记录天数差；
5. IV 和 HV 均使用一致的年化约定与交易日口径；具体 `252/365` 选择必须版本化。

建议字段：

```text
matched_term_iv
matched_term_days
term_match_method
atm_contract_ids
annualization_basis
quote_quality
```

## 6. 信号解释与 Gate

`IV < HV30` 仅表示期权隐含的未来波动低于近期已经实现的波动。它可能是合理折价：近期一次跳空
会抬高 HV，而未来波动可能快速回落。第一版至少同时检查：

- `iv_hv_ratio` 与标的自身历史 percentile；
- HV30 是否仍在上升，以及 `hv_trend_10d_20d_60d`；
- IV 与 HV 的期限是否匹配；
- 财报或重大宏观事件是否落在期权期限内；
- HV30 是否被近期单日跳空显著污染；
- bid/ask、成交量、OI 和可交易合约数量；
- Expected Move 与近期真实振幅；
- Theta/期限风险所需的数据是否齐全。

第一版候选逻辑只能输出研究标签：

```text
波动率折价
+ HV 没有快速回落
+ 期限匹配合格
+ 存在可识别催化剂或持续振幅
+ 流动性合格
= long_vol_candidate
```

`short_vol_candidate` 还必须额外检查事件尾部风险、负 Gamma 环境和可定义的最大损失。任何候选标签
都不等于交易建议。

`long_vol_score` 和 `short_vol_score` 在没有回测校准前不得伪装成概率。V1 可以输出 0–100 的可解释
规则分，但必须同时返回分项、权重和 `score_type=heuristic_unvalidated`；正式策略应在 point-in-time
历史数据上重新校准并版本化。

## 7. 与 CTA 信号的组合规则

CTA 提供方向和调仓压力，IV/HV 提供波动率相对定价；两者不能混成一个方向分数：

- CTA 趋势明确且 IV/HV 折价：标记“方向明确、波动率可能偏便宜”，但不自动选择期权结构。
- CTA 接近翻转且 IV/HV 折价：提高转折/凸性观察优先级。
- CTA 趋势明确但 IV/HV 显著溢价：提示期权成本或事件溢价，不能因此反向做 CTA。
- CTA 信号混乱且 IV/HV 折价：只能说明波动率候选，不能推断上涨或下跌。
- CTA 与期权结构冲突时，AI 输出 `confirmed | mixed | conflicting | unknown` 并引用证据路径。

后续如果生成期权策略，必须由独立、有限风险且可回测的策略层完成；当前股票综合 Agent 仍只把
期权指标用作入场、波动风险和目标区上下文。

## 8. 建议 Schema

每个 symbol 的 overview 增加：

```json
{
  "iv": 35.429,
  "hv_30d": 58.328,
  "matched_term_iv": 35.429,
  "term_match_method": "provider_composite_proxy",
  "iv_hv_spread": -22.899,
  "iv_hv_ratio": 0.6074,
  "iv_hv_regime": "deep_discount",
  "iv_hv_percentile": null,
  "iv_hv_history_count": 0,
  "hv_trend_10d_20d_60d": {
    "10d": null,
    "20d": null,
    "60d": null
  },
  "event_adjusted_flag": "unknown",
  "long_vol_score": null,
  "short_vol_score": null,
  "model_fidelity": "proxy",
  "warnings": []
}
```

`compare_option_observations` 至少增加：

- `hv_30d` before/after/delta；
- `iv_hv_spread` before/after/delta；
- `iv_hv_ratio` before/after/delta；
- `iv_hv_regime_changed`；
- 显式 `from_phase`、`to_phase` 和各自时间戳。

所有 AI 返回必须复制确定性数值或引用其证据路径，不能自行重新计算后输出不同结果。

## 9. 前端需求

期权面板至少增加：

- HV30；
- IV−HV30；
- IV/HV30；
- 折价/匹配/溢价标签；
- 数据时间、期限匹配方式和 fidelity；
- 有足够历史后展示 symbol-specific percentile 和 HV trend；
- 事件调整、流动性不足或一次性跳空污染的警告。

UI 不得仅用红绿颜色把“折价”显示成自动利多或把“溢价”显示成自动利空。

## 10. 实施顺序与验收

> 实施状态（2026-08-13）：Phase A 与 Phase B 的代码、测试和前端链路已完成；Phase C 属于
> 下一阶段的数据工程与回测工作，不应在缺少匹配期限历史序列时伪造 percentile、trend 或 score。

### Phase A：修正两次 AI 调度

- [x] `pre_market` 和 `post_close_review` 创建真实 Agent Session。
- [x] `pre_close` 完成采集、冻结和报告，但模型调用数严格为 0。
- [x] 收盘复盘只评分盘前预测，同时仍能引用尾盘 Observation。
- [x] 同日血缘、cutoff 和回放无未来数据泄漏。

### Phase B：现有综合 IV/HV 第一版

- [x] 从 overview 确定性生成 spread、ratio、regime 和质量字段。
- [x] 修复 `compare_option_observations` 的 overview 路径并加入 HV 比较。
- [x] Agent Prompt 明确期限错配、事件污染和“非方向信号”约束。
- [x] 前端展示 HV30、spread、ratio 和标签。
- [x] 使用 2026-08-04 AMZN/MSFT/GOOG/AAPL/QQQ 快照作为冻结回归夹具。

### Phase C：正式期限匹配和历史标准化

- [ ] 建立每日 30D ATM IV、HV30 和 IV/HV point-in-time 序列。
- [ ] 完成期限总方差插值、报价质量过滤和历史 percentile。
- [ ] 增加 HV 10/20/60 日趋势与 event-adjusted flag。
- [ ] 对候选阈值、score 和 CTA 组合规则做 walk-forward 回测；记录成本、滑点和幸存者偏差。

### 最低验收标准

1. AMZN 样本输出约 `spread=-22.899`、`ratio=0.607`、`deep_discount`。
2. QQQ 样本输出约 `spread=-1.065`、`ratio=0.959`、`matched`。
3. HV30 缺失或为零时无除零错误，结论为 unknown。
4. Phase 比较返回真实 IV、HV30、spread 和 ratio 变化，不再静默返回空 IV。
5. 尾盘运行即使启用 Agent，也不会发出模型请求。
6. 收盘复盘能读取尾盘数据，但报告中不存在尾盘 AI 预测或其评分。
7. CTA、IV/HV 和 AI 文本均可追溯到同一个冻结 dataset 和 evidence path。
