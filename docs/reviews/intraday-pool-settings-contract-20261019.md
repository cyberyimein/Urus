# 日内观察标的池设置契约

日期：2026-10-19。实现范围是独立的日内观察池设置、按交易日冻结的 collector plan，以及冻结业务分组的 capture/API 投影。日内主页面布局由 root 负责。本轮只做本地离线开发，不部署、不连接生产或真实 provider。

## 设置 API

`GET /api/settings/intraday-pool` 返回 `status`、池 `revision`、`active`、`pending`、固定基准、可选成员目录和不可用原因。`source` 位于各版本的 `active` / `pending` 对象中。成员目录来自当前全局 Universe；日内池接受其中已启用且可报价的标的，不额外按 asset type 限制。每个成员带 `symbol`、`display_name`、`themes` 和默认业务分组。设置不会修改全局 Universe，也不触发它的即时生效行为。

首次尚无日内池配置时，默认必须延续旧 collector 的实际日内股票 capture，而不能从 `instrument_validation_symbols` 初始化。离线检查确认该字段缺省会落到 18 个，而 10/17 冻结的现网 `equity-intraday` task 与其最近实际 capture 都是包含 SPY/QQQ/IWM 的 46 个唯一代码。运行时只读 sidecar 最新身份有效的股票 capture envelope 顶层 `requested_symbols`（不 hydrate payload，也不要求 `quality.complete` 或 scheduler 标记完成），再逐项从全局 Universe 目录解析名称/theme；目录或 capture 缺成员、读取失败时返回 `unconfigured/unavailable`，不自动修剪、扩展或改池。若没有历史 capture，设置可手动挑选成员，但新选择只在下一个交易日生效；当日 plan 仍 unavailable，collector 不用静态配置悄悄兜底。`SPY`、`QQQ`、`IWM` 是不可取消的固定基准，实际成员最少三只；它们始终进入报价与分钟线范围，并显示为“大盘基准”。三者原本就在现网 46 代码窗口内，不能因此扩大 distinct-code 范围。10/17 私有冻结 TOML 只用于离线兼容对照，产品代码不读取或硬编码其路径。

`PUT /api/settings/intraday-pool` 接收 `{base_revision, selected_symbols}`。保存只写入/替换 `pending`，生效日为保存当日之后的首个 XNYS 交易日。若在一个交易日再次保存，则先把已到生效日的 pending 提升为 active，再把新选择安排到下一个交易日；同一天多次保存只更新下一交易日 pending。生效日按 `exchange_calendars` 的 XNYS 会话计算，覆盖周末、节假日、提前收市和 DST，不按自然日加一。

## Collector plan 与日冻结

`GET /api/intraday/collection-plan?trading_date=YYYY-MM-DD` 接受 XNYS 会话日，按日期解析 active/pending，返回：

```json
{
  "schema_version": "urus.intraday.collection_plan.v1",
  "plan_id": "<sha256>",
  "status": "configured",
  "trading_date": "2026-10-20",
  "pool_revision": 2,
  "selected_symbols": ["AAPL", "MSFT"],
  "benchmark_symbols": ["SPY", "QQQ", "IWM"],
  "member_symbols": ["AAPL", "MSFT", "SPY", "QQQ", "IWM"],
  "quote_symbols": ["AAPL", "MSFT", "SPY", "QQQ", "IWM"],
  "minute_symbols": ["AAPL", "MSFT", "SPY", "QQQ", "IWM"],
  "display_group_by_symbol": {"AAPL": "消费电子", "MSFT": "软件", "SPY": "大盘基准", "QQQ": "大盘基准", "IWM": "大盘基准"}
}
```

plan_id 是不含可变时间戳的 canonical plan 内容 SHA-256。每个交易日首个股票维护/采集任务在到期 runner 中通过后端 plan 加载器取得 plan 后，先在 collector 的 `collector.sqlite` 用 `trading_date` 主键写入；同一日期之后重启、再保存设置、重试或分片都只读该冻结记录。`equity-intraday`、`equity-maintenance`、G1/G2/G3 与 finalizer 共用同一个 plan；静态配置中的旧 `symbols` 不与新 plan 混并。获取失败或状态未配置时，当前股票采集槽终止并明确记录不可用/跳过，不切回静态池；维护任务保持可重试直到开盘前截止。其它既有收市、日线和期权任务照常运行。

业务分组取全局 Universe 的主 theme（即 `themes` 第一项，兼容旧 `theme`）；三个固定基准统一显示为“大盘基准”。`G1/G2/G3` 是采集时段，`A/B/C/D` 是内部样本/容量角色，两者都不作为行业展示标签。原有角色分配算法不因池设置而改变。

## Capture 与读取契约

股票分片 capture 与完整股票 capture/finalizer 都在 `data` 中保存 `pool_plan_id` 与冻结的 `display_group_by_symbol`。后端 `GET /api/intraday/overview` 与 symbol projection 的 `symbols[symbol].observation_group` 为可选 `string | null`，值来自同一个 capture 的冻结映射，不读取最新 pending 设置来重标旧 bars。历史 capture 没有此字段时返回 null；前端按既有 A/B/C/D 角色显示，并标明这是采集角色。

## 验收重点

- 首次默认池逐项沿用最新身份有效旧 capture 的请求代码（即使该槽为 partial），并与 Universe 目录完全匹配；缺失时显式 unavailable。SPY/QQQ/IWM 不可取消，且始终在报价和分钟线范围。
- PUT 不改变 `/api/settings/universe`，同交易日和重启不改变当天冻结 plan；新设置只在下一 XNYS 会话生效。
- 周五、周末、XNYS 假期、DST 前后都按真实 XNYS session 日期计算。
- collector 每个日期只冻结一次；plan 获取失败时不混入静态 symbols 或重新切分当天池；维护、G1/G2/G3、finalizer 使用一致成员和主题映射。
- capture projection 从历史 capture 自身读取 `observation_group`；旧 capture 返回 null。
- 新增单元/集成测试均离线；不连接 provider。前端只做类型和 Vite 构建，不写 UI 单测。
