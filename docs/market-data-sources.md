# 市场数据来源与收市观察边界

本文只描述当前运行链路。所有外部数据都保留来源、采集时间和质量状态；缺失数据使用 `unavailable` 或 `partial`，不填入旧值或虚构的实时值。

## 一次 Observation Run 的数据流

```text
同步 Universe
    ↓
冻结 Observation Group 版本
    ↓
读取/补齐 canonical daily_bars
    ↓
计算指标、图表投影和确定性策略
    ↓
串行读取期权快照并计算结构
    ↓
写入 Observation Run 与收市后邻近标记
```

股票日线和期权采集共享主机级 `MoomooCollectionCoordinator`。历史日线先经过 `HistoryCapacityService` 的容量计划和 `HistoryAdmission` 请求前复核；期权元数据、期权链和合约快照使用独立的限频类别，但仍在同一采集锁内串行执行。

## 数据源矩阵

| 数据 | 来源 | 采集方式 | 主要用途 |
| --- | --- | --- | --- |
| 美股/ETF 报价 | Moomoo OpenD | 批量 snapshot | 当前价格、成交、来源状态 |
| 完整日线 OHLCV | Moomoo OpenD | 按准入逐标的读取 | `daily_bars`、指标与图表 |
| 期权标的快照 | Moomoo OpenD | 不订阅，按期权队列读取 | Option Spot 与收市后对照 |
| 期权链/合约快照 | Moomoo OpenD | 元数据与链路按限频串行读取 | DEX、GEX、Gamma Wall、Max Pain |
| 日频宏观 | FRED / Yahoo | 可选 HTTP 适配器 | 宏观上下文和交叉校验 |
| AI 决策 | Anomalo Workflow | 用户主动提交冻结输入 | 仲裁，不改写事实层 |

## 股票证据

`MoomooMarketAdapter.instrument_cards()` 返回两类内容：

- `instruments`：前端个股页使用的报价、日线技术指标、主题和相对基准字段；
- `history_snapshots`：只用于把返回的历史 K 线写入 `daily_bars`，不作为页面事实的第二份来源。

`DailyMarketEvidenceService` 只从 `daily_bars` 构建 `Daily Decision Dataset`，再生成 `Decision Chart Projection` 和策略结果。辅助标的（例如期权底层）可以出现在证据来源中，但不会因为期权队列而消耗历史日线槽位。

## 期权证据

每个配置标的可包含多个到期日。每个到期日独立计算：

- Max Pain：按 Open Interest 和执行价计算；
- DEX：`delta × open_interest × contract_multiplier × spot`；
- GEX：`gamma × open_interest × contract_multiplier × spot² × 1%`，并显式记录 calls/puts 的建模符号假设；
- DEX/Gamma Walls、Gamma Zones、Strike GEX sign changes；
- ATM Expected Move、IV/HV 关系和 Spot Gamma Profile。

合约链的完整计算结果与页面展示范围分离：页面可以裁剪远离现价的执行价行，但 totals 和 walls 仍基于完整链路计算。

## 收市后邻近标记

`build_post_close_option_alignment()` 对每个 `symbol + expiration` 比较官方 `regular close` 与 Max Pain、Call DEX Wall、Put DEX Wall、Net DEX Wall。默认邻近阈值为 ±0.60%。

结果字段含义：

- `near_max_pain`：收盘价落在 Max Pain 邻近阈值内；
- `near_dex_wall`：收盘价落在任一 DEX Wall 邻近阈值内；
- `dex_influence_candidate`：结构性邻近候选，不表示 DEX 已经造成收盘；
- `flags`：页面用于高亮的确定性标记。

如果官方收盘价不可用，`OPTION SPOT` 只作为参考位置，`status=unavailable`，不得生成正式邻近标记。盘后价或 last-price fallback 会保留在来源信息里，但不会冒充 regular close。

## 质量与容量状态

日线容量状态和数据质量独立保存：

- `access_state`：`not_requested`、`admitted`、`pending_quota`、`collecting`、`retry_wait`、`acquired`、`disabled`；
- `quality_state`：`unknown`、`stale`、`partial`、`ready`。

例如历史样本过短可以是 `acquired + partial`，不能被显示为额度不足。额度无法读取时，系统 fail-closed，不发起新的历史 K 线请求，同时保留 Universe 的采集意图。
