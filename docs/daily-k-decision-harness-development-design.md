# 日 K Decision Harness 开发设计

> 状态：已确认方向，等待分阶段实现  
> 适用范围：Urus 日 K 数据、指标可视化、算法策略、个股/组/观测组决策，以及未来 Anomalo Workflow 执行接入  
> 不包含：直接预测今日大盘方向的 AI、自动交易、当前阶段的 AI 执行代码、动态更新 Skill 代码  
> Phase D 按钮、输入输出与实际 Anomalo 运行 Interface：
> [Phase D AI 决策流程与按钮设计](phase-d-ai-decision-flow-design.md)

## 1. 本文结论

Urus 下一阶段不继续开发“直接预测今日大盘方向”的 AI。主线改为一个以完整日 K 为事实基准、无需 AI
也能独立产生研究建议的 **Decision Harness**：

```text
日 K 数据
  → 指标与组级状态
  → 多个确定性 Strategy Decision
  → 个股 / 组 / 收市后观测组页面
  → 主动提交 Decision Workflow Definition
  → Anomalo 对齐并执行 Workflow JSON
  → Urus 接收结构化结果并保存审计记录
  → 后续 Decision Case / 回放 / Skill 演化
```

运行职责分成两个平面：

- **Urus 是设计与事实平面**：拥有数据、指标、策略、组定义、Workflow JSON、版本、触发入口、结果投影和决策账本。
- **Anomalo 是 AI 执行平面**：拥有 Workflow 执行器、节点调度、模型与 Skill 运行、并发、重试、超时和执行轨迹。

Urus 不复制 Anomalo 的 AI 执行能力；Anomalo 也不成为行情事实或策略算法的权威来源。两者通过版本化
Workflow Interface 形成明确 Seam。

当前实现顺序是：

1. 日 K 数据与指标可视化；
2. 算法策略与确定性建议；
3. 个股、组和收市后观测组产品页面；
4. 只完成 Anomalo Workflow Interface 的设计，不实现 AI；
5. 最后实现 Decision Case、回放评价和 Skill 演化。

## 2. 目标和非目标

### 2.1 目标

第一阶段完成后，即使 Anomalo 完全不可用，用户也能：

- 在单个股票页面查看完整日 K、技术指标、质量状态和全部策略建议；
- 在组页面查看主题整体强弱、广度、离散度、领涨与落后股票；
- 配置若干 Observation Group；
- 在收市后自动生成一次确定性观测报告；
- 主动决定是否对个股或组发起 AI 评估；
- 追溯每条建议使用的数据、策略版本和产生时间。

未来接入 Anomalo 后，用户还能：

- 从个股页面主动发起单股 AI 决策；
- 从组页面主动发起组级 AI 决策；
- 从指标和策略横截面的卡片中主动筛选异常值、状态突变和值得关注的对象；
- 查看 Workflow Binding、执行状态、结构化结果和失败原因；
- 对同一冻结输入重跑不同 Workflow、Skill 或模型版本，而不覆盖旧结果。

### 2.2 非目标

- 不恢复“AI 直接预测今日大盘涨跌”的开发。
- 不让新闻或 SNS 情绪直接覆盖日 K 策略。
- 不让 AI 自行采集未冻结的最新行情或补齐缺失事实。
- 不把策略信号直接变成订单。
- 不在 Urus 内再实现一套模型编排、Skill 调度和 AI 节点执行器。
- 不在当前阶段动态修改 Anomalo Skill。
- 不依据一次成功或失败自动升级策略、Workflow 或 Skill。

## 3. 核心设计原则

### 3.1 V1 必须在没有 AI 时完整可用

AI 是可选增强，不是页面能否给出建议的前置条件。数据质量、指标、策略输出、组级状态和收市后报告全部由
确定性代码完成。

### 3.2 完整日 K 是统一时间基准

所有 V1 正式策略只读取已经完整收盘的日 K。盘前报价、盘中价格、RSS 和 SNS 可以作为独立信息展示，
但不能悄悄进入日 K 策略。

每个数据集必须保存：

- `trading_date`：最后一根允许使用的完整日 K 日期；
- `cutoff_time`：冻结证据的实际时间；
- `market_timezone`：交易所时区；
- `bar_completion_policy`：如何判定 K 线已完成；
- `source`、`adjustment` 和质量状态；
- 输入范围及内容 hash。

### 3.3 指标不是策略

RSI14、MACD、均线和布林带只是事实特征。一个 Strategy Module 必须结合上下文，输出完整的
**Strategy Decision**，不能把 `RSI < 30` 直接包装成买入建议。

### 3.4 组决策不是个股分数平均值

组级判断必须使用组级特征：广度、中位数收益、离散度、相对强弱、成交量参与、领涨集中度和内部退化。
组内股票评分只是一类输入。

### 3.5 功能后做，账本先记

Decision Case、案例检索和 Skill 演化可以后实现，但每次确定性策略运行从第一版就必须保存冻结输入、全部
策略输出和后续可评价的 horizon。否则后续无法重建真实案例。

### 3.6 流程在 Urus，执行能力在 Anomalo

Urus 保存并版本化 **Decision Workflow Definition**；Anomalo 根据其中声明的节点、依赖、输入和输出执行。
Urus 只验证远端回执的身份、hash 和输出 Schema，不重复执行 AI 节点。

## 4. 逻辑架构

```text
┌──────────────────────────────── Urus ────────────────────────────────┐
│                                                                      │
│  Daily Market Evidence Module                                       │
│  日 K → 指标 → 相对强弱 → 组级状态 → 冻结 Daily Decision Dataset     │
│                          │                                           │
│                          ▼                                           │
│  Strategy Registry Module                                           │
│  运行全部适用 Strategy Adapter → Strategy Decision[]                │
│                          │                                           │
│                          ▼                                           │
│  Decision Workspace Module                                          │
│  个股页面 / 组页面 / Observation Group / 收市后确定性报告            │
│                          │                                           │
│                     用户主动触发 AI                                  │
│                          ▼                                           │
│  Remote Workflow Module                                             │
│  编译 Workflow JSON → 对齐 → 提交 → 跟踪 → 验收 → 持久化             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ Workflow Interface
                           ▼
┌────────────────────────────── Anomalo ───────────────────────────────┐
│ Workflow alignment / DAG execution / Skill & model invocation       │
│ bounded evidence fetch / retry / timeout / trace / structured output│
└──────────────────────────┬───────────────────────────────────────────┘
                           │ result + receipt + trace reference
                           ▼
┌──────────────────────────────── Urus ────────────────────────────────┐
│ Result Projection + Decision Ledger                                 │
│ 页面展示 → 后续结果追加 → Decision Case → Evaluation → Skill 候选    │
└──────────────────────────────────────────────────────────────────────┘
```

这里有五个需要保持 Depth 的 Module：

| Module | Interface | Implementation 隐藏的复杂度 | Leverage / Locality |
| --- | --- | --- | --- |
| Daily Market Evidence | `freeze(scope, trading_date) -> Daily Decision Dataset` | 数据源、补数、复权、质量、指标、组聚合 | 所有页面和策略共享同一事实定义 |
| Strategy Registry | `evaluate(dataset, scope) -> Strategy Decision[]` | 策略发现、适用性、版本、顺序、异常隔离 | 策略可独立新增、回放和评分 |
| Decision Workspace | `get_view(scope, dataset_id)` | 个股/组/计划任务的查询和展示投影 | 页面不自行拼装决策事实 |
| Remote Workflow | `submit(binding, frozen_input) -> Remote Decision Run` | Binding 校验、幂等、远端状态、事件恢复和结果验收 | Anomalo 替换和测试集中在一个 Seam |
| Decision Ledger | `record/open/resolve` | 不可变决策、结果追加、评价窗口、血缘 | 后续案例和学习不污染在线路径 |

Remote Workflow Seam 至少有两个 Adapter：

- `AnomaloWorkflowAdapter`：未来生产实现；
- `FakeWorkflowAdapter`：测试和本地 UI 状态验证。

因此这个 Seam 是真实可测试的，不是只为未来假设创建的抽象。

## 5. 决策范围

统一使用 **Decision Scope** 描述一次分析对象：

| `scope_type` | 含义 | 触发方式 | 输出重点 |
| --- | --- | --- | --- |
| `instrument` | 单个 symbol | 个股页面主动触发 | 单股策略冲突、风险和建议 |
| `group` | 一个版本化 Observation Group | 组页面主动触发 | 组趋势、广度、领涨/落后和个股排序 |
| `observation_run` | 多个已配置组的一次收市后运行 | 计划任务自动触发 | 组间比较、变化、异常和重点列表 |

建议的统一结构：

```json
{
  "scope_type": "group",
  "scope_id": "optical-module",
  "scope_version": 3,
  "symbols": ["AAOI", "COHR", "LITE"],
  "benchmark_symbols": ["QQQ", "SPY"],
  "trading_date": "2026-08-21"
}
```

同一个 symbol 可以属于多个组。任何正式决策必须冻结当时的 `scope_version` 和 symbol 列表；修改组成员不能
改变历史报告。

## 6. 日 K 数据设计

### 6.1 当前实现和迁移方向

现有 `InstrumentDailyBarModel` 隶属于一次 `InstrumentSnapshotModel`，每次采集可能重复保存同一批 260 根历史
K 线。它适合快照审计，但不适合扩大到大量 Observation Group 后作为长期日线主存储。

目标设计是增加规范化 Daily Bar Module：

```text
daily_bars
  unique(symbol, exchange, bar_date, adjustment, source)
```

建议字段：

```text
symbol / exchange / asset_type
bar_date / market_timezone
open / high / low / close / volume / turnover
adjustment / currency
source / source_revision
collected_at / corrected_at
quality_status / content_sha256
```

迁移期间：

1. 保留现有快照表，避免破坏 Stage 4B；
2. 新日 K 流程优先写入规范化表；
3. Daily Decision Dataset 只引用 `symbol + date range + content hash`，不复制全量 K 线；
4. 需要完整审计时可按引用重建输入；
5. 旧快照读取通过 Adapter 转换为同一 Daily Bar Interface。

### 6.2 数据质量 Gate

每个 symbol 产生：

```text
ok
partial
stale
missing
conflicted
```

至少检查：

- 日期连续性和交易日历；
- OHLC 合法性；
- 成交量非负；
- 最后一根 K 线是否完整；
- 复权方式是否一致；
- 重复来源是否冲突；
- 指标要求的最少样本是否满足；
- benchmark 是否可用。

`missing/conflicted` 不运行正式策略；`partial/stale` 由各策略声明是否允许，并必须进入 Strategy Decision 的
`quality` 字段。

### 6.3 Daily Decision Dataset

新增 **Daily Decision Dataset**，作为日 K 产品线的冻结证据包，不要求沿用旧 Stage 4B 的盘前/收盘前配对模型。

```json
{
  "schema_version": "urus.daily_decision_dataset.v1",
  "dataset_id": "uuid",
  "trading_date": "2026-08-21",
  "cutoff_time": "2026-08-22T05:30:00Z",
  "scope": {},
  "bar_manifest": [],
  "indicator_snapshot_ids": [],
  "group_snapshot_ids": [],
  "news_event_ids": [],
  "quality": {},
  "content_sha256": "..."
}
```

内容一旦冻结不可修改。数据源之后发生修订时创建新数据集版本，不回写旧数据集。

## 7. 指标与可视化

### 7.1 指标分级

P0 直接复用并补齐现有 `backend/app/analytics/technical.py`：

- 收益：1/5/20/60/120/252 日；
- 均线：10/20/50/100/200 日及价格距离；
- RSI14：当前值、变化和复合上下文；
- MACD 12/26/9；
- Bollinger 20/1、20/2、20/3 和带宽；
- ATR14 和 ATR 百分比；
- 10/20/60 日实现波动率；
- 20 日成交量比和量价 effort/result；
- 20/60/252 日高低点距离；
- 对 SPY、QQQ 和组 benchmark 的相对强弱。

P1 在 P0 稳定后增加：

- ADX14 / +DI / -DI；
- Donchian 20/55；
- OBV 或 Accumulation/Distribution；
- Stochastic 或 Williams %R；
- 缺口和连续涨跌状态；
- 趋势斜率、波动收缩和突破距离。

增加指标必须满足三个条件：有明确计算定义、有页面用途、至少被一个 Strategy Module 使用或被用户明确需要。
不为了“指标数量多”保存重复含义的指标。

### 7.2 指标版本

每次指标输出保存：

```text
feature_version
input_bar_hash
as_of_trading_date
method / parameters
quality_status / warnings
calculated_at
```

同一输入和 `feature_version` 必须产生相同结果。算法修订创建新版本，不能覆盖旧结果。

### 7.3 可视化目标

个股页面不能再以“把所有指标数字排列出来”为主要表达。数字适合程序和 AI 精确读取，但人类寻找机会时更需要
快速识别趋势、拐点、压缩、突破、背离、策略冲突和距离关键价位还有多远。

页面默认进入 **图形决策模式**，数字表降级为图表光标详情和“数据明细”抽屉。用户应当在 5–10 秒内回答：

- 当前是上升、下降、盘整还是状态不明；
- 价格接近哪个确认位或失效位；
- 成交量是否确认当前走势；
- 动量正在增强、减弱还是背离；
- 哪些策略刚刚触发，哪些仍在等待；
- 当前机会是刚形成、已经拥挤，还是已经失效；
- 相对大盘和所在组是走强还是走弱。

图表不是装饰。每个可视元素必须能追溯到 Daily Decision Dataset、指标或 Strategy Decision，不从像素位置
反向制造新的业务结论。

### 7.4 TradingView 式个股工作区

V1 不复制完整 TradingView，而实现交易判断最有价值的一组能力：K 线、指标窗格、缩放平移、同步十字光标、
图层开关、策略标记、关键价位和 benchmark 对比。

桌面默认布局：

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ NVDA  日 K  · 2026-08-21 · Quality OK     3M 6M 1Y 3Y MAX    [AI 评估]    │
├──────────────────────────────────────────────────────────┬──────────────────┤
│ 图层：MA20 MA50 MA200  BB  策略 事件 关键位              │ 当前决策摘要     │
│                                                          │ aligned / watch  │
│  ┌────────────────────────────────────────────────────┐  │                  │
│  │ Candle + MA + Bollinger + 策略区域/标记            │  │ 策略卡片         │
│  │ 同步十字光标 / 缩放 / 平移 / 价格轴                │  │ 趋势：看多       │
│  └────────────────────────────────────────────────────┘  │ 均值回归：等待   │
│  ┌────────────────────────────────────────────────────┐  │ 突破：接近确认   │
│  │ Volume + Volume MA                                 │  │                  │
│  └────────────────────────────────────────────────────┘  │ 确认位 / 失效位  │
│  ┌────────────────────────────────────────────────────┐  │ 风险和质量       │
│  │ RSI14 或 MACD，可切换/叠加窗格                     │  │                  │
│  └────────────────────────────────────────────────────┘  │ [展开全部证据]   │
│  ┌────────────────────────────────────────────────────┐  │                  │
│  │ Relative Strength：vs SPY / QQQ / Group Benchmark  │  │                  │
│  └────────────────────────────────────────────────────┘  │                  │
├──────────────────────────────────────────────────────────┴──────────────────┤
│ 状态时间带：趋势 ━━━  动量 ━━━  波动 ━━━  策略触发 ◆ 事件 ●             │
└─────────────────────────────────────────────────────────────────────────────┘
```

右侧决策摘要固定在可视范围内，但宽度控制在图表区域的 25%–30%。用户折叠摘要后，图表占满页面。移动端不做
强行缩小的双栏，而改为“图表 → 当前建议 → 策略详情”的纵向顺序。

### 7.5 主 K 线图层

主图至少支持以下 Layer：

| Layer | 默认状态 | 图形表达 | 用途 |
| --- | --- | --- | --- |
| Candlestick | 开 | 完整 OHLC 蜡烛图 | 价格结构是主视觉 |
| MA20 / MA50 | 开 | 两条可区分实线 | 中短期趋势和回踩位置 |
| MA200 | 开 | 较粗长周期线 | 长期 regime |
| MA10 / MA100 | 关 | 可选线 | 避免默认图层过多 |
| Bollinger 20/2 | 关 | 中轨 + 半透明带 | 波动压缩和偏离 |
| Donchian 20/55 | 关 | 阶梯边界 | 突破确认 |
| Strategy Overlay | 开 | 标记、价格线和区间 | 把策略结论落到价格图上 |
| Event Marker | 开 | 时间轴图标 | 财报、公司和新闻事件 |
| Data Quality | 开 | 缺口阴影/顶部告警 | 防止对坏数据形成错觉 |

颜色和线型必须由统一 Visual Token 定义。均线不因股票涨跌改变颜色；看多/看空颜色只用于策略状态，不污染指标
本身。默认 Layer 控制在 6 个以内，其他内容通过图层菜单主动开启。

### 7.6 Strategy Overlay：把建议画到图上

Strategy Decision 不能只停留在右侧数字卡片。每条策略可产生一组纯展示用 Overlay：

```text
trigger_marker       信号首次成立的 K 线
confirmation_line    需要突破或站稳的确认价格
invalidation_line    策略失效价格
setup_zone           可观察但尚未确认的价格区间
risk_zone            策略明确认为风险扩大的区间
horizon_window       从决策日向后投影的观察窗口
evidence_marker      指标背离、量价确认或数据异常的位置
```

视觉语法：

- `▲ / ▼`：已确认的 bullish / bearish trigger；
- `◇`：等待确认的 setup，不表现为已成交信号；
- 实线：确认位；虚线：失效位；
- 半透明区域：setup/risk zone；
- 横向淡色时间区：horizon，而不是价格目标预测；
- `!`：数据风险或重大事件；
- 策略冲突时，在同一日期使用上下分层标记，不互相覆盖。

点击策略卡片只高亮该策略的 Overlay，其他策略降低透明度；再次点击恢复全部。hover 标记显示：策略名称、版本、
触发日期、stance、action、原因、确认/失效条件和 Evidence Reference。

Overlay 只能来自后端提供的结构化位置。前端不能解析自然语言如“接近 MA20”后自行推测画线价格。

### 7.7 “抓住时机”的视觉表达

当前机会不能只显示一个 score。右侧摘要和主图共同表达 **Setup Progress**：

```text
forming → near_confirmation → confirmed → extended → invalidated
形成中      接近确认             已确认       已过度延伸    已失效
```

该状态由 Strategy Module 确定性输出，并至少包含：

```text
stage
stage_since
confirmation_distance_atr
invalidation_distance_atr
bars_in_stage
changed_from_previous_stage
```

页面表现：

- 主图突出当前 setup zone、确认位和失效位；
- 摘要使用有方向的阶段轨迹，不使用仅有百分比的仪表盘；
- “接近确认”显示距离确认位还有多少 ATR，而不是制造精确胜率；
- `extended` 显示价格已偏离短期均线或风险位，提醒不是所有强势都仍是好时机；
- 阶段刚变化时展示“今日新变化”，比静态状态更醒目；
- 不满足质量 Gate 时阶段固定为 `unknown`。

### 7.8 同步指标窗格

所有窗格共享同一时间轴和十字光标：

- 移动主图光标，Volume、RSI、MACD 和 Relative Strength 同时定位到同一交易日；
- 顶部 legend 实时显示该交易日 OHLC、涨跌幅、成交量和已开启指标值；
- 缩放或平移任一窗格，其他窗格同步；
- 双击价格轴恢复自动缩放；
- 双击时间轴回到默认 1Y；
- `←/→` 可逐根 K 线移动，便于复盘；
- 点击 Evidence Reference 自动切换需要的 Layer、移动到日期并短暂高亮。

副图默认只打开 Volume 和一个动量窗格，避免垂直空间被五六个指标同时占满。用户可将 RSI、MACD、Relative
Strength、ADX 加入或移出窗格，并保存个人图层预设。

### 7.9 Benchmark 对比

主图提供两种对比模式：

1. `Price`：只看标的实际价格；
2. `Performance`：从当前可见窗口首日归一为 100，对比 symbol、SPY、QQQ 和 Group Benchmark。

Performance 模式只用于相对表现，不能和实际价格轴叠在一起。Relative Strength 窗格显示比率或累计超额收益，
并明确 benchmark、计算窗口和 as-of date。

### 7.10 状态时间带

K 线下方增加紧凑的 regime/state timeline，把离散指标翻译为可扫描的历史状态段：

```text
Trend      ▓▓▓ bullish ▓▓ neutral ▓▓▓▓ bearish
Momentum   ▓ strengthening ▓ weakening ▓ negative
Volatility ▓ compressed ▓ expanding ▓ normal
Volume     ▓ confirm ▓ diverge ▓ quiet
Strategy   ◆ trend trigger   ◇ mean-reversion setup   × invalidated
Events     ● earnings        ! risk event
```

时间带用于回答“状态何时改变、持续多久”，不是再显示一排当前数字。点击某个状态段会缩放到该时间区间，并在右侧
显示对应判定依据。

### 7.11 图表交互模式

提供三个预设，避免用户每次从几十个 Layer 中配置：

| 模式 | 默认内容 | 适用场景 |
| --- | --- | --- |
| 决策 | K 线、MA20/50/200、策略 Overlay、Volume、状态时间带 | 日常快速寻找时机 |
| 技术 | K 线、可编辑指标窗格、完整图层菜单 | 深入技术检查 |
| 复盘 | 决策时点、当时可见数据、horizon 和后续 outcome | Phase E 案例回放 |

V1 实现“决策”和“技术”；“复盘”先保留数据契约，Phase E 再启用。图层和窗口预设保存在用户设置中，不写入
Decision Dataset，也不改变策略输出。

### 7.12 Chart Projection Interface

后端提供 **Decision Chart Projection**，前端只负责渲染和交互：

```json
{
  "schema_version": "urus.decision_chart_projection.v1",
  "dataset_id": "uuid",
  "scope": {"scope_type": "instrument", "scope_id": "NVDA"},
  "timezone": "America/New_York",
  "price": {
    "symbol": "NVDA",
    "price_format": {"precision": 2, "currency": "USD"},
    "bars": [
      {"time": "2026-08-21", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
    ]
  },
  "series": [
    {"series_id": "ma20", "pane": "price", "kind": "line", "points": []},
    {"series_id": "rsi14", "pane": "momentum", "kind": "line", "points": []}
  ],
  "overlays": [
    {
      "overlay_id": "uuid",
      "strategy_decision_id": "uuid",
      "kind": "confirmation_line",
      "price": 0,
      "start_time": "2026-08-21",
      "end_time": null,
      "label": "突破确认",
      "evidence_refs": []
    }
  ],
  "state_segments": [],
  "events": [],
  "quality": {},
  "content_sha256": "..."
}
```

`Decision Chart Projection` 是只读展示投影，不是新的事实来源。它集中处理指标序列、pane、Overlay、事件和状态段
之间的映射，为个股页、组内 mini chart 和未来复盘提供 Leverage 与 Locality。

### 7.13 图表渲染实现约束

当前前端没有金融图表依赖，已有图形以手写 SVG 为主。主 K 线不继续用手写 SVG 扩展；平移、缩放、十字光标、
多 pane、价格轴和数百根蜡烛会让页面实现变成难以维护的浅层逻辑集合。

实现时选择一个支持 Canvas、candlestick、time scale、price scale、markers 和 resize 的成熟金融图渲染器，包在
一个深的 `DecisionChartWorkspace` Module 内。页面只传入 Decision Chart Projection 和用户显示设置。图表库选择
通过独立 spike 确定，本文不锁定供应商。

性能要求：

- 1Y/3Y 日 K 在普通桌面设备上首次可交互时间目标小于 500ms（不含网络）；
- 十字光标移动不触发 Vue 全页面响应式重渲染；
- 只更新变化的 series/markers，不销毁重建整个 chart；
- 页面隐藏时停止无意义 resize/hover 工作；
- 10 年以上数据按可见窗口加载或下采样，但 OHLC 不得用普通平均破坏高低点；
- 组页的小图使用轻量 sparkline/line projection，不同时创建几十个完整 K 线实例。

### 7.14 可访问性和视觉安全

- 上涨/下跌同时使用颜色、形状和文字，不依赖红绿色觉；
- 所有 Layer 有图例和可见开关；
- 键盘可移动十字光标和打开标记详情；
- Canvas 图表同时提供当前可见窗口的无障碍摘要；
- tooltip 固定单位、日期、数据来源和质量；
- `insufficient_data` 使用缺口纹理和文字，不画成中性行情；
- Strategy Overlay 与真实成交标记视觉不同，避免被误认为已执行交易；
- 用户可打开精确数据表，但默认不让数字表抢占主视图。

## 8. Strategy Registry

### 8.1 Strategy Interface

每个 Strategy Adapter 使用统一 Interface：

```text
evaluate(Daily Decision Dataset, Decision Scope) -> Strategy Decision[]
```

统一输出：

```json
{
  "schema_version": "urus.strategy_decision.v1",
  "decision_id": "uuid",
  "dataset_id": "uuid",
  "scope": {},
  "strategy": {
    "name": "trend_momentum",
    "version": "1.0.0",
    "implementation_sha256": "..."
  },
  "stance": "bullish",
  "action": "watch",
  "horizon": {"unit": "trading_day", "value": 5},
  "score": 62,
  "score_scale": [-100, 100],
  "confidence": null,
  "confidence_type": "heuristic_unvalidated",
  "setup_progress": {
    "stage": "near_confirmation",
    "stage_since": "2026-08-21",
    "confirmation_distance_atr": 0.35,
    "invalidation_distance_atr": 1.4,
    "bars_in_stage": 2,
    "changed_from_previous_stage": true
  },
  "reasons": [],
  "risks": [],
  "confirmation_conditions": [],
  "invalidation_conditions": [],
  "visual_anchors": [],
  "evidence_refs": [],
  "quality": {},
  "generated_at": "..."
}
```

允许值：

- `stance`：`bullish | bearish | neutral | insufficient_data`；
- `action`：`prioritize | watch | wait | avoid | no_action`；
- `score`：只表达策略内部强弱；不同策略的 score 在校准前不能假定为同一概率；
- `confidence_type`：未经样本验证必须是 `heuristic_unvalidated`，不能伪装成胜率。
- `setup_progress`：策略当前所处阶段和到确认/失效位的 ATR 距离；
- `visual_anchors`：可直接转换为图上 trigger、price line、zone 或 marker 的结构化位置；没有可画位置时为空。

### 8.2 第一批策略

Phase B 首批实现五个完整 Strategy Adapter：

1. `trend_momentum_v1`：均线结构、趋势、MACD、相对强弱和成交量确认；
2. `mean_reversion_v1`：偏离、RSI、Bollinger、ATR 和反转确认；
3. `breakout_volume_v1`：20/60 日突破、波动收缩、成交量和失败突破；
4. `relative_strength_rotation_v1`：相对 benchmark、组内排名和持续性。
5. `quality_left_side_reversal_v1`：面向现金股票的复合左侧反转策略；以研究范围和流动性作为可执行资格 Gate，联合 RSI12、缺口/成交密集支撑区、Beta 调整相对强弱与量价确认，输出 `ineligible | no_setup | watching | armed | confirmed | invalidated` 状态。

`rsi14_context` 保留为指标 Module 或上述策略的输入，不单独作为“RSI 低就买”的浅层策略。

`quality_left_side_reversal_v1` 不包含期权执行或订单能力。当前日 K 证据只能验证研究范围、流动性和技术结构，不能从 OHLCV 推导护城河或基本面反转；因此 Strategy Decision 必须持续暴露“基本面资格仍需人工或后续基本面快照确认”的风险，不得把用户加入观察范围解释为基本面已通过。

### 8.3 策略运行规则

- 每次运行全部适用策略，不能只运行当前最受信任的策略；
- 单个策略失败不阻止其他策略输出；
- 保存 `not_applicable` 和原因，不静默消失；
- 输出排序只用于稳定展示，不代表优先级；
- 策略不得调用 LLM；
- 策略不得读取其他策略的自然语言结论；
- 策略不得访问 cutoff 之后的数据；
- 策略版本和输入 hash 是回放主键的一部分。

## 9. 确定性综合建议

在 AI 之前增加透明的 Deterministic Synthesis，但不提前平均掉策略冲突。

输出至少包括：

```text
consensus_state: aligned | mixed | conflicted | no_signal | insufficient_data
bullish_count / bearish_count / neutral_count
strongest_supporting_strategy_ids
strongest_conflicting_strategy_ids
suggested_action
conflict_summary
```

V1 规则建议：

- 两个以上风格不同策略同向且无强反向策略：`aligned`；
- 有信号但强弱不同：`mixed`；
- 趋势与均值回归等核心策略方向相反：`conflicted`；
- 全部 neutral/not_applicable：`no_signal`；
- 关键数据质量失败：`insufficient_data`。

页面必须同时显示综合建议和原始 Strategy Decision。综合建议是方便阅读的投影，不是新策略，也不能删除冲突。

## 10. Observation Group 与组级分析

### 10.1 Observation Group

一个组至少包含：

```text
group_id / display_name / description
version / status
symbols / benchmark_symbols
tags / display_order
created_at / activated_at
```

组版本不可变。编辑操作创建新版本；收市后运行冻结当时所有 active 版本。

### 10.2 组级特征

每个交易日计算：

- 组内有效/缺失 symbol 数量；
- 1/5/20/60 日收益的中位数和四分位数；
- 站上 MA20/50/200 的比例；
- RSI 分布和极端值比例；
- MACD 正/负比例；
- 成交量放大参与比例；
- 对 SPY、QQQ 和组 benchmark 的相对强弱；
- 横截面离散度；
- 前 1/3 名贡献度和领涨集中度；
- 领涨、改善、恶化和落后 symbol；
- 与前一交易日组状态的变化。

### 10.3 组级判断

组级 Strategy Module 应回答：

- 行情是否由足够多股票参与；
- 是否只有一两只股票撑住组指数；
- 领涨股是否继续增强；
- 组内是否出现广度背离；
- 组相对大盘是否改善或恶化；
- 哪些个股是组内正异常和负异常。

组页面默认包含：

```text
组强弱时间线
广度图（MA20/50/200）
收益/RSI/相对强弱热力图
组内排名和变化
领涨集中度
全部组级与个股 Strategy Decision
确定性组建议
主动 AI 评估按钮
```

### 10.4 组级可视化工作区

组页面不默认铺开每只股票的几十个指标。第一屏以四张互补的图回答“这个组是否值得关注，以及机会集中在哪里”：

```text
┌──────────────────────────────┬──────────────────────────────┐
│ 组相对强弱 + 组中位数走势    │ 市场广度时间序列             │
│ vs SPY / QQQ / benchmark     │ % > MA20 / MA50 / MA200      │
├──────────────────────────────┼──────────────────────────────┤
│ 轮动象限图                    │ 个股状态热力图               │
│ 相对强弱 × 动量变化           │ symbol × date / indicator    │
└──────────────────────────────┴──────────────────────────────┘
```

#### A. 组相对强弱图

- 组收益使用等权中位数或明确声明的组指数，不默认用市值加权；
- 同图显示 SPY、QQQ 和 benchmark 的归一化走势；
- 背景标记组级 Strategy Decision 的 forming/confirmed/invalidated 区间；
- 下方可叠加横截面离散度，识别“指数强但内部撕裂”。

#### B. 市场广度图

- 三条线显示 `% > MA20`、`% > MA50`、`% > MA200`；
- 显示有效样本数，避免成员缺失造成假改善；
- 标记广度与组价格方向不一致的背离区间；
- hover 显示当日进入/退出均线之上的 symbol，而不只显示百分比。

#### C. 轮动象限图

每个 symbol 是一个点：

```text
X：相对强弱水平
Y：相对强弱 5 日变化或动量变化
点大小：成交量参与或组内权重
箭头：过去 5 日移动方向
形状：Strategy stance
```

四个象限表达“领先并增强、领先但减弱、落后但改善、落后且恶化”。点击点后，右侧打开该 symbol 的简化 K 线和
策略摘要；双击进入完整个股页面。

#### D. 个股状态热力图

热力图支持两个视图：

- `Snapshot`：行是 symbol，列是 trend/momentum/volume/volatility/relative strength/strategy；
- `Timeline`：行是 symbol，列是最近 N 个交易日，颜色和形状表示综合状态。

默认按“今日变化幅度”排序，而不是固定 alphabetic 排序，让刚转强、刚转弱和刚触发的股票出现在顶部。用户可切换
为强弱、组内排名、成交量异常或 symbol 排序。

### 10.5 Small Multiples

组内个股列表提供统一比例的小图集合，用于识别形态而不是精确读数：

- 每张显示最近 60 日收盘线、MA20、MA50、成交量异常点和策略 trigger；
- 同一组使用一致的时间窗口，但价格轴各自归一化；
- 不在小图中塞入 RSI/MACD 等完整副图；
- 默认只渲染可视区域，避免几十个图同时消耗资源；
- 点击小图在页面内展开完整 `DecisionChartWorkspace`。

### 10.6 组图表选择联动

选择热力图行、象限点或排行榜项时，所有组图聚焦同一 symbol；选择某个交易日时，广度、热力图和小图同步到该日。
筛选只改变显示，不重算或修改冻结的组级 Strategy Decision。

### 10.7 指标横向扫描页面

个股页回答“这一只股票的指标和策略如何”，指标页回答“同一个指标在全部观察组和全部成员中处于什么状态”。指标页是
Observation Run 冻结结果的只读横向投影，不创建新的指标算法，也不在前端重新计算指标。

建议路由：

```text
/indicators
/indicators/:indicatorId
```

`/indicators` 展示指标目录和当前横向摘要；`/indicators/:indicatorId` 展示一个确定指标版本，例如
`rsi14@technical_v4`、`macd_12_26_9@technical_v4` 或 `relative_strength_20d@technical_v4`。页面必须绑定一个已完成的
Observation Run，并固定显示其 trading date、cutoff、feature version 和组版本集合。切换日期时切换到另一份已保存投影，
不能把不同交易日或不同 feature version 的值拼在同一排名中。

页面默认包含：

```text
指标定义、参数、单位和版本
全部观察组的分布与极端值摘要
group → symbol 两级状态表
symbol × group / date 热力图
当前值、分位数、阈值距离和最近变化
刚进入 / 刚离开阈值的 symbol
数据缺失、过期和不可比标记
跳转个股页与所属组页
“AI 寻找指标异常”占位按钮
```

核心展示规则：

- 第一层按 Observation Group 分组，组标题显示有效样本数、中位数、四分位数和极端值占比；
- 第二层展示组内全部 symbol，同一 symbol 属于多个组时可在各组中出现，但底层只引用同一个 indicator snapshot；
- 默认排序优先显示“刚发生变化”的 symbol，其次是阈值距离和绝对极端程度，不默认按 symbol 字母排序；
- RSI 应显示当前值、30/50/70 区间、进入或离开超买超卖区的时间；MACD 应显示 DIF/DEA/柱体及交叉状态；
- 均线类指标应显示价格相对均线的位置、距离和刚上穿/下穿状态，不能只显示 MA 数字；
- 相对强弱必须同时展示 benchmark、窗口和 excess return，禁止把不同 benchmark 的数值直接混排；
- 表格、热力图和分布图只使用后端投影中的正式值，前端只负责坐标、颜色、筛选和排序；
- 点击 symbol 在侧栏打开简化 K 线与指标上下文，双击进入 `/instruments/:symbol`；
- 页面筛选不修改 Observation Group，也不生成新的正式判断。

指标页不是单纯的数字表。第一屏至少用“跨组分布图 + 分组热力图 + 状态变化列表”回答：哪些组整体偏强或偏弱、哪些
股票处于极端、哪些股票刚发生指标状态转换。

2026-08-26 的本地实现将 `group → symbol` 的第二层落地为密集卡片网格，而不是固定最小宽度的横向表格。卡片必须继续引用同一份
Observation Run 冻结投影，前端只负责布局、坐标和视觉派生，不重新判定指标状态。每个卡片显示 symbol、正式状态、当前值、前值/变化、
质量状态、状态转换和到个股冻结数据集的链接；卡片区域不得产生横向滚动。

组列表按新版关注列表语义拆成“指标推荐”和 `SECTOR WATCHLIST / 主题观察组` 两个区段；旧的手工自选/核心观察组不进入当前横向扫描的
活动展示。投影中的历史质量统计仍保留在 provenance 中，页面顶部的活动组和活动标的统计只计算当前可见区段。

RSI 14 使用 0–100 的三段确定性轨道：`<30` 为超卖、`30–70`（含边界）为平衡、`>70` 为超买；组级轨道显示状态分布、Q1、
Median、Q3 和区间颜色，个股卡片显示当前值与前值 marker。超卖、平衡和超买使用独立的冷青、暗沙和暖红色，但颜色只表示状态区间，
不得被解释为买卖指令。`threshold_distance` 若包含 50 等参考线，UI 不得把它笼统显示为“距阈”，应显示为“距参考”或按状态边界展示。

其它横向指标沿用相同卡片容器，根据指标类型使用零轴发散条、比例区间轨道、二态线上/线下轨道或策略状态轨道；score 只能标注为策略
内部强弱，不能显示为胜率或收益概率。卡片网格在宽屏、窄屏和移动端自适应列数，唯一允许横向滚动的是顶部的指标选择栏。

策略横向扫描使用独立的策略观察组设计：组级同时显示 `bullish / neutral / bearish` stance 分布和 `forming / near_confirmation /
confirmed / invalidated` setup stage 分布；score 轨道固定为 `-100` 至 `+100`，以 `0` 为中心，并按策略规则用 `-25`、`+25` 区分偏空、
中性和偏多区域。个股卡片必须按“stance → action → setup stage → score → change/quality”顺序阅读，score 是策略内部强弱，不能被解释为
概率、胜率或收益预测。策略颜色只表达 stance 和阶段状态，不能替代确认条件、失效条件或 Evidence Reference。

### 10.8 策略横向扫描页面

策略页回答“同一个确定性策略对全部观察组股票给出了什么判断”。它读取已经持久化的 Strategy Decision，并按策略名称、
版本和 Observation Run 进行横向投影，不在页面中再次执行策略。

建议路由：

```text
/strategies
/strategies/:strategyId
```

`/strategies` 展示 Strategy Registry、适用范围和本次运行摘要；`/strategies/:strategyId` 固定到明确的
`strategy_name + strategy_version + implementation_sha256`。页面默认包含：

```text
策略定义、版本、horizon 和适用条件
各观察组 bullish / bearish / neutral / not_applicable 分布
group → symbol 两级 Strategy Decision 表
stance / action / setup stage 热力图
forming / confirmed / weakening / invalidated 状态变化
trigger、确认位、失效位和距离
策略内领涨、掉队、刚触发和接近触发列表
质量失败、策略异常和不适用原因
Evidence Reference 与个股图表跳转
“AI 寻找策略关注项”占位按钮
```

核心展示规则：

- 策略页只能比较同一 strategy version 和 implementation hash；版本不同必须拆开展示；
- `score` 只表示策略内部强弱，界面不得改写为胜率或收益概率；
- 默认优先显示刚确认、接近确认、刚失效和强冲突的 symbol，而不是只按 score 排序；
- 每个决策必须同时展示 stance、action、setup stage、horizon、确认条件、失效条件和质量状态；
- `not_applicable`、`insufficient_data` 和 `error` 必须分开，不能统一显示为 neutral；
- 组摘要使用该策略的真实决策分布与参与率，不使用所有策略综合分数替代；
- 点击决策可定位到个股 K 线上的 Strategy Overlay，并继续进入完整个股页；
- 页面可以把其他策略的结论作为冲突提示，但不能在当前策略排名中偷偷混入其他策略分数。

策略页第一屏至少用“跨组 stance 分布 + setup stage 热力图 + 状态变化泳道”回答：策略目前主要在哪些组生效、哪些股票
刚形成机会、哪些判断正在失效，以及哪些结果因数据质量不能成立。

### 10.9 横向扫描投影与 AI 占位

指标页和策略页不增加新的 Decision Scope。它们都引用一个不可变的 `observation_run_id`，并使用 lens 描述当前观察角度：

```json
{
  "scope_type": "observation_run",
  "scope_id": "observation-run-id",
  "lens": {
    "type": "indicator",
    "id": "rsi14",
    "version": "technical_v4"
  }
}
```

或：

```json
{
  "scope_type": "observation_run",
  "scope_id": "observation-run-id",
  "lens": {
    "type": "strategy",
    "id": "quality_left_side_reversal_v1",
    "version": "1.0.0",
    "implementation_sha256": "..."
  }
}
```

建议提供只读查询 Interface：

```text
GET /api/observation/indicator-catalog
GET /api/observation/runs/:runId/indicators/:indicatorId
GET /api/observation/strategy-catalog
GET /api/observation/runs/:runId/strategies/:strategyId
```

横向投影响应至少包含：

```text
schema_version / content_sha256
observation_run_id / trading_date / cutoff_time
lens / feature_version 或 strategy identity
group_version_ids / dataset_ids / snapshot_ids
groups[]：分布、参与率、状态计数和质量
rows[]：group_id、symbol、正式值/Decision、变化和 Evidence Reference
transitions[]：前态、现态、发生日期和触发原因
quality：有效、缺失、过期、冲突和不可比计数
```

后端 workspace Module 负责去重、版本一致性检查、跨组聚合、变化比较和 content hash。前端不得直接拉取几十只股票后自行
拼出正式横向排名。若同一 Observation Run 中找不到唯一版本，接口返回明确的 `version_conflict`，页面要求用户选择版本，
不能静默选择“最新版本”。

Phase C 只实现确定性投影和 AI 按钮占位。按钮必须显示“尚未接入”或 disabled 状态，点击不得调用现有聊天接口、不得生成
模拟结果，也不得创建未完成定义的远端运行。Phase D 冻结 Workflow Definition，并启用真实提交和结果展示。

## 11. 三种用户流程

### 11.1 个股主动决策

```text
打开 /instruments/:symbol
  → 选择冻结到最近完整日 K 的数据集
  → 页面展示指标和确定性策略
  → 用户点击“AI 评估”
  → Urus 创建 instrument scope 的 Workflow JSON
  → Anomalo 对齐并执行
  → 页面显示远端状态和结构化结果
```

页面加载不会自动调用 AI。刷新页面也不会创建新 Remote Decision Run。

### 11.2 组主动决策

```text
打开 /groups/:groupId
  → 冻结 group version 和数据集
  → 展示组级与个股策略
  → 用户点击“AI 评估整个组”
  → 提交 group scope Workflow JSON
  → Anomalo 在一个远端工作流中完成组分析
```

组内 symbol 数量过大时，由 Workflow Definition 明确 map 并发上限和汇总节点；Urus 不拆成 N 次互不关联的
模型调用。

### 11.3 收市后观测组数据采集工作流

系统只有一个正式自动数据入口：收市后的 Observation Run。它不自动调用 AI。

```text
确认交易日已收市
  → 从已部署系统同步并保存当前 Universe Revision
  → 同步并冻结 active Observation Group 版本
  → 增量补齐全部 symbol 日 K
  → 质量 Gate
  → 计算指标与组级特征
  → 运行全部适用策略
  → 与上一交易日比较
  → 保存 deterministic-only 报告
```

计划时间必须基于目标市场交易日历、`market_timezone`、正式收盘时间和可配置的数据到齐缓冲，而不是简单按服务器本地日期运行。
调度器必须持有任务级互斥锁，同一交易日同一 slot 不能并发执行；进程重启后从已保存 stage 恢复或幂等重跑。
`--once post_close_observation` 仅用于人工联调和补跑，仍执行相同的 sync、股票与期权冻结、质量和报告链路，不能走旁路；`post_close_review` 仅表示独立的 AI 复盘阶段。

确定性报告至少回答：

- 哪些组正在改善、转弱或异常；
- 哪些组技术结构最强/最弱；
- 哪些个股显著领先、掉队或发生状态变化；
- 哪些判断因数据质量不足不能形成；
- 哪些策略出现显著冲突。

Observation Run 的结束条件是数据、策略和 deterministic-only 报告完成；它不创建 Remote Decision Run。指标和策略横截面页可以在之后由用户主动使用这份冻结证据寻找异常卡片和关注项。

### 11.4 已部署系统的关注列表是自动运行的上游事实源

Phase C 不维护第二份自动关注列表。只要配置了 `OBSERVATION_UNIVERSE_SOURCE_URL`，每次正式盘后运行必须先通过
`GET {OBSERVATION_UNIVERSE_SOURCE_URL}/api/settings/universe` 读取已部署 Urus 当前 Universe，再生成本地可审计版本。禁止直接
读取远端数据库、共享目录或进程缓存，也禁止把某次获取到的 symbol 清单硬编码进仓库。

同步顺序固定如下：

```text
拉取上游 Universe
  → 校验响应 Schema、symbol、asset_type、roles、tags
  → 规范化并计算 source content hash
  → 保存不可变 Local Universe Revision
  → 根据该 revision 同步自动 Observation Group
  → 冻结本次运行引用的 group version
```

自动组生成规则：

- 只选取 `enabled=true` 且 `roles.equity_watchlist=true` 的成员；股票和 ETF 都必须保留；
- 所有入选成员进入一个 `core-watchlist` 组，产品名称为“指标推荐”，并带有 `indicator-recommendation` 标签；每个有效
  `theme` 生成一个主题组；
- “指标推荐”是部署 Universe 的只读投影，设置页只能查看和筛选，不能手动勾选或取消成员；同一 symbol 可以同时属于指标推荐
  和多个主题组，不能因为跨组重叠被删除；
- 自动组必须保存 `source_url`、上游 revision/hash、本地 Universe Revision ID 和同步时间；
- 上游不再返回的自动组应退役，但不得删除历史版本、历史运行，也不得改动手工组；
- 上游内容 hash 未变化时，同步必须复用已有 revision 和 group version，不制造空版本。

“指标推荐”是独立的自动列表；`SECTOR WATCHLIST` 只展示由当前 Universe 的 `theme` 投影生成的主题组。主题归属可在
Universe 设置页自由维护，不再提供“自选组 / 自选个股”这类特殊 Observation Group。为兼容旧版本，带有
`user-qualified`、`self-selected` 或 `user-selected` 标签的旧手工组只保留用于历史引用，不出现在 active group 列表、侧栏或默认盘后运行中；
其历史 group ID、版本和运行均不迁移、不删除。

正式同步入口为 `POST /api/observation/groups/sync`。响应至少返回：来源标识、上游 revision/hash、本地 revision ID、创建/复用/
退役的组、成员数和同步时间。日志、错误和 API 响应中的 URL 必须移除用户名、密码、token 和敏感 query 参数。

### 11.5 同步失败、陈旧数据与幂等策略

默认策略是 fail closed：配置了上游来源但本次无法成功同步时，不允许悄悄改用 `ENABLED_SYMBOLS`、上次内存状态或空 Universe
启动新的正式 Observation Run；已经保存的历史报告仍可读取。这样可以避免系统看似正常、实际观察错误标的。

如运维确实需要容灾，可显式设置 `OBSERVATION_ALLOW_STALE_UNIVERSE=true`。此时只能复用最近一次成功保存的 Local Universe
Revision，并必须在 run 和 report 中记录 `universe_freshness=stale`、最后成功同步时间、失败原因和可见警告。默认值必须为
`false`，不能由代码自动打开。

幂等边界至少包括：

- Universe Revision：规范化后的上游内容 hash；
- Group Version：group ID、成员/角色/标签 hash 与来源 revision；
- Observation Run：交易日、全部冻结 group version、策略/指标 policy version；
- Dataset/Snapshot：run ID、去重 symbol 集、benchmark 集、数据截止时间和 schema version。

重复触发相同盘后槽位应返回或复用同一份逻辑结果；如果输入发生变化，应明确创建新版本，不能原地覆盖旧报告。

### 11.6 一次运行只冻结一份共享市场数据集

调度器在同步完成后，对全部 active group 的成员和所需 benchmark 求并集并去重，一次性完成日 K 补齐、质量 Gate、指标计算和
Strategy Decision。所有组快照都引用同一个 run-level dataset；重叠 symbol 必须复用相同 bar、feature snapshot 和 Strategy
Decision ID，不能按组重复采集或得到不同的“同日事实”。

相对强弱等依赖 benchmark 的投影必须保存 benchmark ID 和版本，并拒绝在同一比较面板中混用不同 benchmark。每个报告项都
必须能追溯到 `observation_run_id → group_version_id → dataset/snapshot ID → evidence reference`。

单个 symbol 的采集或计算失败不得让整次运行消失。运行可进入 `partial`，但必须保留失败 symbol、失败阶段、错误类别、重试次数
及其影响的组；报告中不得把缺失值当成零值或把失败成员从分母中无声移除。

### 11.7 deterministic-only 报告契约

关闭 AI（`URUS_AGENT_ENABLED=false`）且没有任何模型密钥时，盘后流程仍必须完整结束并产出
`urus.observation_report.v1`。基础报告至少包含：

- Universe 来源/revision/freshness、group version、dataset 和 policy provenance；
- 组强弱排序、改善/恶化、广度变化和状态转换；
- 个股领先、掉队、刚触发、接近触发、刚失效及数据异常；
- 机会泳道、风险泳道、策略冲突和质量问题；
- Group Momentum Map、Breadth Delta 和状态转换所需的冻结数据；
- report schema version、稳定 content hash、生成时间和运行状态。

“异常”必须来自版本化阈值、横截面分布或相邻交易日状态差分，不能只是任意截取 top K。相同冻结输入和相同 policy version 必须
生成相同业务内容 hash；时间戳、数据库 ID 等运行元数据不得污染该 hash。AI 入口在 Phase C 只能作为占位能力，自动任务不得
调用聊天接口、Anomalo 或任何模型服务。

## 12. Urus 与 Anomalo 的职责

| 能力 | Urus | Anomalo |
| --- | --- | --- |
| 日 K、指标、新闻和组事实 | 拥有和冻结 | 只按 manifest 读取 |
| Strategy Decision | 确定性产生并保存 | 不重算、不修改 |
| Workflow 流程设计 | 保存、版本化、编译 JSON | 对齐并解释 |
| Workflow 节点实现 | 不复制 AI 执行能力 | 拥有节点 Adapter 和调度器 |
| Skill / 模型执行 | 不执行 | 执行并记录版本 |
| 节点并发、重试、超时 | 不实现远端 DAG | 负责 |
| 远端输出 Schema 校验 | 验收 envelope 和最终 artifact | 在节点和最终输出阶段执行 |
| 触发权限和幂等 | 负责 | 校验 idempotency key |
| 决策结果与产品展示 | 保存并投影 | 返回结果/轨迹引用 |
| 后续实际结果 | 拥有 | 可按未来 Workflow 读取 |
| Decision Case / Skill 演化 | 设计和批准 | 未来执行候选生成/验证节点 |

关键规则：

- Urus 是 Workflow Definition 的权威来源；
- Anomalo 返回的 `accepted_definition_sha256` 必须等于本次提交 hash；
- Anomalo 不得把远端保存的“同名最新 Workflow”替换本次 JSON；
- 远端节点可以读取 Urus 提供的不可变 evidence bundle，不能查询“最新行情”；
- Urus 不重新运行 AI 节点来验证答案，只验证结构、血缘和状态。

## 13. Decision Workflow Definition

> **实现警告**：第 13—15 节保留了 Anomalo Workflow Runtime 完成前的早期协议草案，其中
> `urus.decision_workflow.v1`、逐次 alignment、请求内携带 Definition 和自定义 Result Envelope 均不是当前远端 Interface。
> Phase D 实现必须使用 Anomalo `docs/integrations/urus-workflow.md` 的正式合同：
> `anomaloharis.dev/workflow/v1`、管理期 validate/import/publish、运行期按精确 `name@version` 调用，以及统一 Run Control。
> 可执行设计以 [Phase D AI 决策流程与按钮设计](phase-d-ai-decision-flow-design.md) 第 9 节为准；不得复制下面的早期 JSON 进入代码。

### 13.1 顶层结构

建议的 V1 JSON：

```json
{
  "schema_version": "urus.decision_workflow.v1",
  "workflow": {
    "workflow_id": "group-strategy-arbitration",
    "workflow_version": "1.0.0",
    "definition_sha256": "computed-with-this-field-omitted",
    "name": "组级策略仲裁",
    "scope_types": ["group"],
    "created_by": "urus"
  },
  "trigger": {
    "mode": "user",
    "source": "group_page"
  },
  "input_contract": {
    "schema_version": "urus.remote_decision_input.v1",
    "required": ["scope", "dataset", "strategy_decisions"]
  },
  "nodes": [
    {
      "node_id": "load_evidence",
      "uses": "anomalo.evidence.load.v1",
      "needs": [],
      "with": {"manifest": "$.input.evidence_manifest"},
      "timeout_seconds": 60
    },
    {
      "node_id": "retrieve_cases",
      "uses": "anomalo.case.retrieve.v1",
      "needs": ["load_evidence"],
      "enabled": false,
      "with": {"top_k": 8},
      "timeout_seconds": 60
    },
    {
      "node_id": "arbitrate_strategies",
      "uses": "anomalo.skill.invoke.v1",
      "needs": ["load_evidence"],
      "with": {
        "skill": "urus-equity-decision",
        "task": "group_strategy_arbitration",
        "input": {
          "scope": "$.input.scope",
          "group_state": "$.evidence.group_state",
          "strategy_decisions": "$.input.strategy_decisions"
        },
        "response_schema": "urus.strategy_arbitration_decision.v1"
      },
      "timeout_seconds": 600,
      "retry": {"max_attempts": 1}
    },
    {
      "node_id": "build_result",
      "uses": "anomalo.output.compose.v1",
      "needs": ["arbitrate_strategies"],
      "with": {
        "schema": "urus.remote_decision_result.v1",
        "decision": "$.nodes.arbitrate_strategies.output"
      }
    }
  ],
  "output_contract": {
    "schema_version": "urus.remote_decision_result.v1",
    "from": "$.nodes.build_result.output"
  },
  "policy": {
    "allow_network": false,
    "allow_latest_data_lookup": false,
    "allow_symbol_expansion": false,
    "max_parallel_nodes": 4,
    "max_total_runtime_seconds": 900,
    "on_optional_node_failure": "continue",
    "on_required_node_failure": "fail"
  }
}
```

计算 `definition_sha256` 时先移除 `workflow.definition_sha256`，再对其余 Definition 做 canonical serialization；
计算完成后才把 hash 写回该字段。数组顺序、数字格式、空字段处理和字符编码必须固定，否则 Urus 与 Anomalo
无法稳定比较 hash。

### 13.2 Workflow 节点约束

每个节点必须声明：

- `node_id`：Workflow 内唯一；
- `uses`：Anomalo 已注册的 capability 名称和 major version；
- `needs`：依赖节点，整体必须是 DAG；
- `with`：静态配置和受限 JSONPath 输入映射；
- `timeout_seconds`；
- `retry`：仅对明确可重试错误使用；
- `enabled`：允许设计中预留但关闭案例节点；
- 输入和输出 Schema。

禁止：

- 任意代码字符串；
- 未声明的网络访问；
- 在节点中读取“latest/current”；
- 节点动态新增 symbol；
- 把模型自由文本当作下游结构化输入；
- Workflow 执行时修改自己的 Definition。

### 13.3 四种 Workflow 模板

V1 设计四个用户主动触发的模板，共享节点能力：

```text
instrument-decision.v1
  load evidence → [retrieve cases later] → arbitrate → result

group-decision.v1
  load evidence → [map instrument context] → group arbitrate → result

indicator-cross-section-review.v1
  load frozen indicator cards → find notable cards → result

strategy-cross-section-review.v1
  load frozen strategy cards → find notable cards → result
```

模板属于 Urus 仓库并接受 code review。Anomalo 只对齐和执行，不成为模板源代码的唯一保存位置。

## 14. 对齐与执行协议

### 14.1 为什么先对齐

Urus 可能引用 Anomalo 尚未部署的 capability、Skill 或 Schema。直接执行只会在运行中晚失败，所以提交分两步：

```text
align(definition) → aligned definition receipt
execute(the exact aligned definition + input manifest) → remote run
```

### 14.2 Alignment Request

```json
{
  "request_id": "uuid",
  "definition": {},
  "expected_executor": {
    "protocol": "anomalo.workflow.v1"
  }
}
```

### 14.3 Alignment Receipt

```json
{
  "alignment_id": "uuid",
  "status": "aligned",
  "accepted_schema_version": "urus.decision_workflow.v1",
  "accepted_definition_sha256": "...",
  "executor_version": "...",
  "resolved_capabilities": [
    {"uses": "anomalo.skill.invoke.v1", "implementation_version": "..."}
  ],
  "resolved_skills": [
    {"name": "urus-equity-decision", "version": "...", "sha256": "..."}
  ],
  "issues": [],
  "expires_at": "..."
}
```

状态：

- `aligned`：允许执行同一 hash；
- `incompatible`：Schema 或 capability 不兼容；
- `missing_capability`：节点未部署；
- `missing_skill`：Skill 不存在；
- `policy_rejected`：权限或资源策略不允许；
- `invalid_definition`：DAG、JSONPath 或 Schema 错误。

只有 `aligned` 可以继续执行。执行时 Alignment Receipt 未过期且 hash 完全一致；任何 JSON 改动都必须重新对齐。

### 14.4 Execution Request

```json
{
  "request_id": "uuid",
  "request_intent_id": "local-uuid-created-once-per-user-action",
  "idempotency_key": "sha256(request_intent_id+scope+dataset+definition)",
  "alignment_id": "uuid",
  "definition": {},
  "input": {
    "schema_version": "urus.remote_decision_input.v1",
    "dataset_id": "uuid",
    "dataset_sha256": "...",
    "scope": {},
    "strategy_decisions": [],
    "deterministic_synthesis": {},
    "evidence_manifest": {
      "mode": "uri",
      "uri": "short-lived immutable bundle location",
      "content_sha256": "...",
      "expires_at": "..."
    }
  },
  "callback": {
    "mode": "poll"
  }
}
```

小型 instrument 输入允许 `evidence_manifest.mode=inline`。组和 observation_run 优先使用 URI + hash，避免把多组
日线重复嵌入 Workflow JSON。远端每个 run 只拉取一次并在 run 范围内缓存。

同一次用户操作的网络重试复用 `request_intent_id`，因此命中同一个 idempotency key；用户明确点击
“重新运行”时创建新的 `request_intent_id`，从而生成新的 Remote Decision Run，但仍引用原 dataset 和 definition。

### 14.5 Execution Receipt 和状态

提交后立即返回：

```json
{
  "remote_run_id": "uuid",
  "status": "accepted",
  "idempotency_key": "...",
  "accepted_definition_sha256": "...",
  "accepted_input_sha256": "...",
  "created_at": "..."
}
```

状态机：

```text
draft
  → aligning
  → alignment_failed | aligned
  → submitting
  → accepted
  → running
  → succeeded | partial | failed | timed_out | cancelled
```

`partial` 只允许可选节点失败且最终 artifact 仍通过 Schema。任何必需节点失败都必须是 `failed`。

### 14.6 Result Envelope

```json
{
  "schema_version": "anomalo.workflow_result.v1",
  "remote_run_id": "uuid",
  "status": "succeeded",
  "definition_sha256": "...",
  "input_sha256": "...",
  "executor_version": "...",
  "started_at": "...",
  "completed_at": "...",
  "artifact": {
    "schema_version": "urus.remote_decision_result.v1",
    "scope": {},
    "arbitration_decisions": [],
    "summary": {},
    "warnings": [],
    "evidence_refs": []
  },
  "node_receipts": [],
  "trace_ref": "...",
  "usage": {},
  "error": null
}
```

Urus 验收顺序：

1. `remote_run_id`、idempotency key 和本地记录匹配；
2. definition/input hash 完全一致；
3. terminal status 合法；
4. artifact 符合声明的输出 Schema；
5. scope、dataset_id 和 symbol 范围未扩大；
6. Evidence Reference 只能指向本次 manifest；
7. 验收成功后创建不可变结果版本。

## 15. Remote Workflow Module 的本地持久化

建议增加以下逻辑实体。

### 15.1 `decision_workflow_definitions`

```text
id / workflow_id / workflow_version
schema_version / definition_json / definition_sha256
scope_types / status(draft|active|retired)
created_at / activated_at
```

同一个 `workflow_id + workflow_version` 内容不可变。

### 15.2 `remote_decision_runs`

```text
id / remote_run_id
dataset_id / scope_type / scope_id / scope_version
workflow_definition_id / definition_sha256 / input_sha256
trigger_mode / trigger_source / requested_by / request_intent_id
idempotency_key / alignment_id
status / attempt
error_code / error_message
created_at / started_at / completed_at
```

### 15.3 `remote_decision_artifacts`

```text
id / remote_decision_run_id
schema_version / artifact_json / artifact_sha256
trace_ref / node_receipts_json / usage_json
accepted_at
```

artifact 是不可变记录；重跑创建新的 Remote Decision Run，不覆盖旧 artifact。

## 16. 页面和路由设计

建议新增产品路由：

```text
/instruments/:symbol
/groups
/groups/:groupId
/indicators
/indicators/:indicatorId
/strategies
/strategies/:strategyId
/observation-groups
/observation-runs
/observation-runs/:runId
/decision-runs/:runId
```

个股图表状态写入 URL，允许刷新和分享当前视图：

```text
/instruments/NVDA?range=1y&mode=decision
/instruments/NVDA?range=6m&mode=technical&panes=volume,rsi,relative_strength
/instruments/NVDA?range=1y&strategy=trend_momentum_v1&focus=2026-08-21
/groups/optical-module?view=rotation&date=2026-08-21&symbol=COHR
/indicators/rsi14?run=observation-run-id&sort=transition&group=optical-module
/strategies/quality_left_side_reversal_v1?run=observation-run-id&stage=confirmed
```

URL 只保存显示模式、可见窗口和选择对象，不保存完整数据或修改 Decision Dataset。未识别的 Layer 和 pane 安全忽略，
并回退到“决策”预设。

### 16.1 公共页面状态

每个决策页分成三块，避免混淆来源：

1. **技术事实**：日 K、指标、组特征和质量；
2. **算法建议**：全部 Strategy Decision 和 Deterministic Synthesis；
3. **AI 评估**：尚未运行、对齐中、运行中、成功或失败。

AI 区域初始状态明确显示“尚未主动发起”，不能用空白或模拟文本冒充结果。

### 16.2 主动触发交互

点击 AI 评估后先显示确认信息：

- scope 和 symbol 数；
- 数据集日期及质量；
- Workflow 名称和版本；
- 预计是否包含案例检索；
- 当前 Anomalo 对齐状态。

确认后创建 Remote Decision Run。按钮在提交期间禁用；相同 idempotency key 的重复点击返回原 run。

### 16.3 收市后报告

Observation Run 页面按回答顺序展示：

```text
今日变化摘要
组强弱排行
改善 / 恶化组
异常强 / 异常弱个股
策略冲突和数据问题
每个组详情
运行和证据详情
```

Observation Run 页面只展示数据采集、确定性分析和证据详情。需要 AI 筛选时，用户进入绑定同一 run 的指标或策略横截面页主动发起。

### 16.4 收市后可视总览

Observation Run 第一屏优先显示变化而不是静态数字：

- **Group Momentum Map**：所有组按相对强弱和 5 日变化落在四象限；
- **Breadth Delta**：每个组今日 MA20/50 广度变化的横向条形图；
- **State Transition Matrix**：组 × 状态变化，如 `forming → confirmed`、`confirmed → weakening`；
- **Opportunity / Risk Lanes**：刚确认、接近确认、过度延伸、刚失效四条泳道；
- **Exception Strip**：数据缺失、重大事件和策略强冲突，不与机会排行混在一起。

点击任一组，页面下半部分加载该组工作区；点击 symbol 继续进入个股完整 K 线。这样用户先找到“哪里正在变化”，
再逐层下钻，不需要阅读全部组的数字表。

## 17. 后端实现映射

建议在不破坏现有 Stage 4B 的前提下新增 `backend/app/decision_harness/`，但保持少数深 Module：

```text
backend/app/decision_harness/
  contracts.py           # 跨 Module 稳定数据契约
  market_evidence.py     # 日 K、指标、质量、组聚合和数据集冻结
  strategies.py          # Strategy Registry 与各 Adapter 的注册/执行
  workspace.py           # 个股、组、Observation Run 的查询与确定性报告
  remote_workflow.py     # Definition 编译、对齐、提交、跟踪和验收
  ledger.py              # 决策记录和后续结果追加；案例功能后启用

backend/app/integrations/
  anomalo_workflow.py    # 未来 AnomaloWorkflowAdapter
```

不要为每个 JSON 转换建立一个只有一两个函数的浅层 Module。上述 Interface 应隐藏持久化、质量 Gate、版本和错误
处理，使页面、计划任务和测试共享同一实现。

现有 `backend/app/integrations/anomalo.py` 是聊天/事件调用 Adapter，Interface 为 `summarize/investigate`。
它不支持 Definition 对齐、DAG 执行、幂等提交和远端状态，因此不能通过增加一个巨大 `message` 字符串来假装
满足 Workflow Interface。未来新增独立 Adapter，并共享底层 HTTP client 配置即可。

## 18. 前端实现映射

建议的页面与可复用展示 Module：

```text
frontend/src/views/
  InstrumentDecisionView.vue
  GroupDecisionView.vue
  IndicatorScannerView.vue
  StrategyScannerView.vue
  ObservationGroupsView.vue
  ObservationRunView.vue
  RemoteDecisionRunView.vue

frontend/src/components/decision/
  DecisionChartWorkspace.vue
  ChartLayerControl.vue
  SynchronizedIndicatorPanes.vue
  StrategyOverlayInspector.vue
  StateTimeline.vue
  DataQualityStrip.vue
  StrategyDecisionCard.vue
  DeterministicSynthesisPanel.vue
  GroupBreadthChart.vue
  GroupRotationMap.vue
  GroupHeatmap.vue
  InstrumentSmallMultiples.vue
  IndicatorCrossSection.vue
  StrategyCrossSection.vue
  CrossSectionHeatmap.vue
  StateTransitionLanes.vue
  ObservationVisualOverview.vue
  RemoteWorkflowStatus.vue
```

前端不得重新计算正式指标、组分数或策略综合结果。图表可以做坐标和显示派生，但业务值来自后端投影。

`DecisionChartWorkspace` 是图形区域的深 Module，内部管理 chart renderer 生命周期、同步时间轴、Layer、Overlay、
tooltip、缩放和 Evidence Reference 聚焦。各页面不直接调用底层图表渲染器，也不为每个指标复制一套 watch/resize/
crosshair 逻辑。删除这个 Module 后复杂度会重新散落到个股、组和复盘页面，因此它通过了 deletion test。

## 19. 失败、重试和恢复

### 19.1 本地确定性流程

- 单 symbol 数据失败：标记该 symbol，其他 symbol 继续；
- benchmark 缺失：依赖相对强弱的策略 `not_applicable`；
- 组有效覆盖低于阈值：组判断 `insufficient_data`；
- 指标异常：保存质量原因，不以默认值代替；
- 进程重启：可根据 dataset/run 状态恢复，不重复创建已冻结数据集。

### 19.2 远端流程

- Alignment 失败不自动降级为聊天接口；
- 网络提交超时后先按 idempotency key 查询，不直接重提；
- `429/5xx/transport_error` 才允许有限重试；
- Schema、policy、missing capability 不重试；
- 用户主动重跑创建新的 attempt，但继续引用同一 dataset；
- 远端失败不删除本地 Workflow JSON、输入 manifest 或确定性报告。

## 20. 性能设计

- 日 K 按 symbol/date 增量补齐，不在每个 Observation Run 重复下载 260 根；
- 指标按 `input_bar_hash + feature_version` 缓存；
- 组状态按 `group_version + trading_date + feature_version` 缓存；
- Strategy Decision 按 `dataset_hash + strategy_hash + scope_hash` 幂等；
- 页面先返回确定性内容，远端 AI 状态独立轮询；
- 大型 evidence bundle 使用 URI + hash，Anomalo run 内只读取一次；
- observation_run 只提交一份远端 Workflow，由 Anomalo 执行组级并发；
- Urus 不在本地复制 Anomalo 的 DAG 调度和模型运行。

## 21. 安全与完整性

- evidence URI 使用短期、只读、run-scoped 凭证；
- Workflow JSON 不能携带长期密钥；
- Anomalo capability 使用 allowlist；
- `allow_network=false` 时节点不得外部检索；
- 输入和输出都保存 SHA-256；
- 用户身份、触发来源和重跑原因进入审计记录；
- 新闻正文遵守来源保存和展示策略，不把未知网页文本当作指令；
- AI 输出只作为研究建议，不进入订单执行 Interface。

## 22. 测试策略

Interface 是主要测试面。

### 22.1 Daily Market Evidence

- 相同 bars 和版本产生相同指标/hash；
- 未完成日 K 不进入正式数据集；
- 复权或来源变化产生新 dataset hash；
- 缺数和冲突进入质量状态；
- 组版本冻结后不受成员编辑影响。

### 22.2 Strategy Registry

- 每个策略使用固定 fixture 做 golden test；
- cutoff 之后数据不可见；
- 单策略异常不阻断其他策略；
- 所有输出符合统一 Schema；
- `heuristic_unvalidated` 不出现伪概率。

### 22.3 Remote Workflow

- Workflow canonical JSON/hash fixture；
- DAG 环、未知节点、错误 JSONPath 在本地编译期失败；
- Fake Adapter 覆盖 aligned/rejected/running/succeeded/partial/failed；
- hash 不一致的 Result Envelope 被拒绝；
- 重复 idempotency key 不创建两个 remote run；
- symbol 或 scope 扩大被拒绝；
- 远端失败时确定性报告仍可访问。

### 22.4 前端

- 个股和组页在 AI 未运行时完整可用；
- AI 状态不会伪装成算法建议；
- Evidence Reference 能聚焦图表；
- K 线、Volume、RSI/MACD 和 Relative Strength 的十字光标与时间轴同步；
- Strategy Overlay 的 trigger、确认位、失效位和区间与后端 projection 一致；
- 图层切换、窗口缩放和页面 resize 不会重建整个图形工作区；
- 组级广度、轮动象限、热力图和 small multiples 使用同一 trading date；
- 选择组内 symbol 后，相关图形和详情联动；
- 数据质量和缺失状态可见；
- 页面刷新不自动发起新 Workflow；
- Observation Run 始终展示 deterministic-only 报告，不出现 AI-enhanced 版本。
- 指标页和策略页只读取同一 Observation Run 的横向投影，不在前端重算指标或策略；
- 同一 symbol 出现在多个组时引用同一个底层 snapshot/decision，组成员关系可以重复展示但证据不能复制成不同版本；
- 不同 feature version、strategy version、implementation hash 或 benchmark 的结果不会被静默混排；
- 指标/策略 AI 按钮在 Phase C 保持 disabled，Phase D 接入后也不会因页面刷新自动提交。

## 23. 分阶段开发计划

### Phase A：日 K 基础与质量

开发：

- 规范化 Daily Bar 存储和旧快照 Adapter；
- 增量补数、交易日历、完整 K 线判定；
- Daily Decision Dataset；
- P0 指标版本化与缓存；
- Decision Chart Projection；
- 数据质量投影。

验收：

- 任意配置 symbol 可稳定查看至少 260 根完整日 K；
- 相同输入可重算得到相同指标和 hash；
- 不再因多个组重复保存同一批历史 K 线。

### Phase B：个股可视化与确定性策略

开发：

- 个股页面和指标图；
- TradingView 式 `DecisionChartWorkspace`、同步十字光标、图层和指标窗格；
- Strategy Overlay、Setup Progress 和状态时间带；
- Strategy Registry；
- 第一批 Strategy Adapter；
- Deterministic Synthesis；
- Strategy Decision 持久化。

验收：

- 不启用 AI 也能看到完整建议；
- 主图能交互查看 1Y 日 K，并与 Volume、动量和相对强弱窗格同步；
- 策略 trigger、确认位、失效位和 setup zone 能从 Evidence Reference 回到原始指标；
- 每条建议有 horizon、风险、确认/失效条件和 Evidence Reference；
- 策略冲突不会被隐藏。

### Phase C：组与 Observation Run

Phase C 实现状态（2026-08-25）：正式入口为 `POST /api/observation/groups/sync` 和 `POST /api/observation/runs`。前者从
`OBSERVATION_UNIVERSE_SOURCE_URL` 指向的已部署 Urus 读取当前 `/api/settings/universe`，保存带来源、revision、content hash 和
freshness 的本地 Universe 版本，并按 `roles.equity_watchlist` 生成“指标推荐”和主题组；后者严格按“同步关注列表 → 冻结组版本 →
一次性冻结全部去重 symbol 的共享 dataset → 组快照/策略 → deterministic-only 报告”执行。整个闭环已在
`URUS_AGENT_ENABLED=false` 下完成真实本地联调：实时上游响应被成功读取，生成 9 个自动组、27 个关注标的，盘后运行成功，
报告为 `urus.observation_report.v1`，所有组复用同一个 run-level dataset，指标/策略横向投影均保持 AI disabled。

侧栏将“指标推荐”作为独立列表；`SECTOR WATCHLIST` 只包含由 Universe 主题自由生成的主题组。Universe 设置页可以筛选
“指标推荐”并查看成员，也可以维护主题归属，但不再提供 `equity_watchlist` 的手动开关；保存时保留上游成员资格，新建标的默认不进入指标推荐。

运行时仍支持没有上游地址的本地开发模式（freshness 为 `local`）；配置上游后默认 fail-closed，只有显式启用
`OBSERVATION_ALLOW_STALE_UNIVERSE=true` 才允许复用最近一次成功 revision，并将 `stale` 和错误原因写入同步结果与报告。

同一 Observation Run 中，重叠组引用相同的 dataset、instrument snapshot 和 Strategy Decision；相对强弱投影拒绝混用
benchmark。报告保存 `urus.observation_report.v1`、内容 hash、改善/恶化组、强弱异常、机会/风险泳道、策略冲突、质量问题、
Group Momentum Map、Breadth Delta 和状态转换，前端只渲染冻结结果。单 symbol 失败保留为显式质量项，不会删除其余组结果。

开发：

- Observation Group 版本；
- 组级特征和组级策略；
- 组页面；
- 组相对强弱、广度、轮动象限、热力图和 small multiples；
- 指标目录、单指标横向扫描页和跨组指标投影；
- 策略目录、单策略横向扫描页和跨组 Strategy Decision 投影；
- 指标/策略页的分组热力图、状态变化列表、个股下钻和 AI 按钮占位；
- 唯一收市后计划任务；
- Observation Run 可视总览；
- deterministic-only 收市后报告。

验收：

- 能同时运行多个已配置组；
- 报告能识别改善/恶化组和异常个股；
- 单个 symbol 失败不会使整次运行消失；
- 能从一个指标视角比较全部观察组成员，且所有值来自同一 Observation Run 和 feature version；
- 能从一个策略视角比较全部观察组成员，且不混用不同 strategy version 或 implementation hash；
- 指标/策略页能优先显示刚转换、刚触发、接近触发和刚失效的 symbol；
- AI 按钮只占位，不调用现有聊天接口或伪造结果。

#### Phase C 实施记录与维护顺序

按以下垂直切片交付，每一片通过测试后再进入下一片，避免先铺 UI、最后才发现数据契约无法闭环：

1. **C1 数据契约与迁移**：定义 Universe Revision、Group/Group Version、Observation Run、共享 Dataset/Snapshot、Report 的表、
   枚举、唯一约束、外键和 Pydantic/TypeScript Schema；迁移必须可从现有数据库前向升级。
2. **C2 上游 Universe 同步**：实现 HTTP client、响应校验、规范化/hash、revision 复用、自动组生成/退役、手工组隔离、凭据脱敏
   和 fail-closed/stale 显式策略。
3. **C3 共享数据集编排**：对组成员及 benchmark 求并集，复用采集、feature 和 Strategy Decision；保存完整 provenance，支持
   单 symbol 失败后的 `partial` 结果。
4. **C4 确定性报告**：实现 `urus.observation_report.v1`、稳定 hash、状态差分、异常阈值、组/个股泳道、冲突和质量摘要；禁止
   任何模型调用。
5. **C5 API 与前端投影**：完成组同步/查询、运行创建/历史/详情、报告读取；组页、总览、指标页和策略页只渲染冻结快照，并可
   下钻到 Evidence Reference。
6. **C6 调度与本地运维**：唯一常驻入口使用 `scripts/schedule_market_data_collection.py`；盘后槽位先 sync 再 run，支持
   `--once post_close_observation`、时区/交易日校验、股票与期权一起冻结、幂等重跑和结构化日志。
7. **C7 真实联调与交付证据**：使用部署实例实时返回的关注列表完成一次本地盘后运行，保存命令、脱敏配置、实际 symbol/group
   数、run ID、report hash、运行状态和关键 API 响应；不得把当次 symbol 清单或数量固化为产品规则。

建议代码落点：`backend/app/core/config.py`、`backend/app/schemas/observation.py`、
`backend/app/repositories/{universe,observation}.py`、`backend/app/services/observation.py`、
`backend/app/decision_harness/observation_report.py`、`backend/app/api/routes/observation.py`、数据库迁移、
`backend/scripts/schedule_market_data_collection.py`，以及前端对应的 API/types/views。若仓库实际结构不同，应保持职责边界而不是
机械创建同名文件。

#### Phase C 必测场景与本地验收

自动化测试至少覆盖：

1. 上游股票与 ETF 都进入“指标推荐”，tags 生成重叠主题组；指标推荐独立展示，`SECTOR WATCHLIST` 只展示主题组，旧自选组不进入 active catalog；
2. 相同上游内容重复同步不新增 revision/group version；
3. 上游删除 tag 只退役自动组，历史版本和手工组保留；
4. 上游超时、非 2xx、坏 JSON、Schema 错误和重复 symbol 均按契约处理并脱敏；
5. 默认 fail closed；显式 stale 模式复用最近成功 revision 并在报告中告警；
6. 多个组重叠成员时只产生一份 run-level dataset 和一套 symbol snapshot/decision；
7. benchmark 不一致时比较被拒绝，而不是静默混算；
8. 相同冻结输入重复生成报告时业务 content hash 一致；
9. 单 symbol 失败生成 `partial` 报告，组分母、失败原因和受影响视图明确；
10. 调度器严格执行 sync-before-run，重复盘后触发幂等，且 AI/Anomalo/chat client 调用次数为零；
11. 前端能查看来源、freshness、group/run 历史、报告版本并下钻到冻结证据。

Luna 完成编码后必须按 README 启动本地后端、前端和调度器，并执行一次：

```bash
cd backend
uv run alembic upgrade head
uv run python scripts/schedule_market_data_collection.py \
  --api-base-url http://127.0.0.1:8000/api \
  --backend-managed-externally \
  --once post_close_observation
```

验收人随后检查 `/api/settings/universe`、`/api/observation/groups`、运行详情和最新报告：来源应为配置的部署实例，成员数与本次
实时响应一致，所有组引用同一共享 dataset，报告具备稳定 hash，且日志中不存在 AI 调用。真实地址和凭据只放本地环境变量，
不能提交到文档、fixture 或版本库。

### Phase D：AI 决策流程与远端接入

Anomalo Workflow Runtime 的开发已经完成，本阶段在 Urus 侧完成 AI 决策产品流程、运行期接入和结果投影。详细基线见
[Phase D AI 决策流程与按钮设计](phase-d-ai-decision-flow-design.md)。首先冻结并核对：

- Workflow JSON Schema；
- capability 命名和版本规则；
- alignment/execution/result 契约；
- evidence bundle 读取方式；
- idempotency、超时、重试和 trace 规则；
- 个股和组两个仲裁 Workflow 模板；
- 指标横向评估和策略横向评估的 observation_run lens 契约；
- `indicator-cross-section-review` 与 `strategy-cross-section-review` Workflow 模板；
- 四类页面按钮的 trigger source、确认信息、输入 manifest 和 Result Schema；
- Anomalo 对齐测试样例。

Workflow Definition 在发布期通过 Anomalo capability manifest 完成 validate、import 和 publish；日常运行按精确 Workflow Ref
调用，不在用户点击时动态发布或逐次 alignment。

指标/策略横向评估 Workflow 必须继续使用 `observation_run` scope。输入 manifest 固定包含本次运行的 group snapshot、indicator
snapshot 或 Strategy Decision ID、版本/hash、质量状态和 Evidence Reference。Workflow 不允许重新查询“最新数据”，也不允许
扩大到当前 Observation Run 之外的 group 或 symbol。idempotency key 至少包含：

```text
observation_run_id
lens.type / lens.id / lens.version / implementation_sha256（若适用）
workflow_id / workflow_version
request_intent_id
```

指标横向 Workflow 必须从当前页面卡片中按重要度找出异常绝对值、异常变化、状态转换、组内分歧和质量可疑项；策略横向
Workflow 必须找出 setup 阶段突变、score 异常、接近确认、新失效、组间分歧和质量可疑项。两者的结果都必须回指具体卡片、
group、symbol 和 Evidence Reference，只能筛选和排序已有证据，不能改写指标、Strategy Decision 或 Deterministic Synthesis。

本阶段实施：

- `AnomaloWorkflowAdapter`；
- 主动个股/组触发；
- 启用指标页“AI 寻找指标异常”和策略页“AI 寻找策略关注项”；
- 提交前确认 observation run、lens、覆盖组数、symbol 数、数据质量和 Workflow 版本；
- indicator/strategy cross-section 远端状态轮询与结构化结果投影；
- 状态轮询和结果投影；
- Fake/production Adapter contract tests。

按钮只有在以下条件全部满足时才启用：绑定的 Observation Run 已完成、lens 对应版本唯一、必要 snapshot/decision 可读、
active Workflow Binding 有效。提交后页面保留原确定性横向视图，并在独立 AI 区域显示 queued/submitting/running/
succeeded/failed/stopping/stopped 状态。部分结论记录在成功 artifact 的 completeness 中，不伪造成远端 `partial` 状态；AI 结果
保存为附加 artifact，不覆盖原横向排名，远端失败时指标页和策略页仍完整可用。

### Phase E：案例、回放和 Skill 演化

最后实施：

- Decision Case 生命周期；
- Strategy Evaluation 和 Arbitration Evaluation；
- point-in-time 相似案例检索；
- 历史回放、walk-forward 和 shadow；
- Skill 候选生成、批准、发布和回滚。

Phase E 虽然后做，Phase B 开始就保存其所需的决策账本字段。

## 24. 后续学习设计

### 24.1 两条评价线保持分离

- **Strategy Evaluation**：策略自身在相同 horizon 的方向、收益、回撤和适用 regime 表现；
- **Arbitration Evaluation**：AI 采信/否决是否优于 deterministic baseline，以及错过哪些未选策略。

所有 Strategy Decision 都必须评价，包括 AI 未采信的策略。

### 24.2 Decision Case

```text
open
  → resolved（horizon 到达并追加实际结果）
  → reviewed（评价和复盘完成）
  → skill_candidate（可选）
```

原始输入、策略输出、AI 输出和检索记录不可回写；实际结果以追加记录关闭案例。

### 24.3 Skill 演化

Anomalo 尚无动态 Skill 更新 Interface 时，Urus 只生成版本化候选文档，不自动发布：

```text
案例集合
  → 候选规律
  → Skill Candidate
  → 历史回放
  → shadow
  → 人工批准
  → 等待 Anomalo 发布 Interface
```

生产 Skill 不允许根据少量近期结果在线自我改写。

## 25. 开发验收总标准

1. AI 关闭时，个股、组和收市后报告仍能完整工作；
2. 正式判断只使用完整日 K，并明确 trading date、cutoff 和 horizon；
3. 每个 Strategy Decision 独立、版本化、可回放；
4. 组级判断使用真实组级特征，而不是个股分数平均；
5. 用户主动触发个股、组或横截面 AI，页面加载不会自动运行；
6. Observation Run 只负责收市数据采集、冻结和确定性报告，不手动或自动调用 AI；
7. Urus 保存 Workflow JSON，Anomalo 对齐并执行相同 hash；
8. Urus 不复制 Anomalo 的 AI DAG 执行能力；
9. 远端失败不影响确定性结果；
10. 所有远端结果可追溯到 dataset、scope、strategy、workflow、skill 和 hash；
11. 决策账本从策略上线第一天开始保存；
12. 不自动交易，不自动修改 active 策略或 Skill；
13. 指标页能从单一指标视角横向比较全部 Observation Group 成员；
14. 策略页能从单一策略视角横向比较全部 Observation Group 成员；
15. 横向页面只投影冻结证据，AI 结果只作为附加解释，不能覆盖正式指标、策略输出或排名。

## 26. 已确定决策与待协议项

### 已确定

- 暂停直接预测今日大盘方向的 AI；
- V1 以完整日 K 为基准；
- 算法策略先于 AI，并已能给出页面建议；
- AI 只能在个股、组、指标横截面和策略横截面由用户主动发起；
- 个股、组、跨组 Observation Run 是三种 Decision Scope；
- 指标页和策略页是 Observation Run 的横向 lens，不新增 Decision Scope；
- AI Workflow 执行能力集中在 Anomalo；
- Workflow Definition 由 Urus 设计和版本化；
- 案例、回放和 Skill 演化最后开发，但账本立即开始记录。

### 与 Anomalo 联调前必须确定

1. Workflow JSON canonical serialization 规则；
2. Alignment Receipt 的有效期和 capability 版本兼容规则；
3. evidence bundle 使用 inline、pull URI 还是二者都支持；
4. 状态使用轮询还是签名 callback；
5. trace 由 Anomalo 返回完整内容还是稳定 `trace_ref`；
6. Skill 的固定版本/hash 如何在 alignment 时解析；
7. observation_run 的最大组数、symbol 数、并发和运行时预算；
8. Result Envelope 的错误码和 retryable 分类。

这些是双方 Interface 的协议项，不阻塞 Phase A、B、C 的本地确定性开发。
