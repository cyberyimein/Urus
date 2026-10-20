# 个股盘前历史预热：实现与验收

2026-10-20。本轮完成本地实现与验收，**未部署**，未连接生产 OpenD、修改生产配置或重启容器。部署后由下一次符合条件的盘前维护任务生效，不能把本地测试结果当作生产已经预热成功。

后续状态：用户追加授权后，已于同日 18:49–18:50 JST 完成 API/collector 部署，见 [部署记录](intraday-prewarm-deployment-20261020.md)。上述“未部署”描述的是本地验收阶段；生产首次预热仍待当日 21:00 维护。

## 实现范围

- 沿用原 collector 的盘前维护任务，原窗口为开盘前 90 分钟启动、开盘前 30 分钟截止。原 Eoflora 任务、盘中 G1/G2/G3 时槽与采集优先级不变。
- collection plan 增加 `asset_type_by_symbol`，纳入 plan 内容哈希；loader 只为 maintenance 注入 `prewarm_symbols`，仅选当日冻结池中类型为 `equity` 的个股。ETF 与大盘基准不新增预热。旧冻结计划缺少类型时保守跳过，不重新冻结、不按 G3 猜类型。
- 检查最近常规交易日、原始 NONE 价格口径下的连续有效 5m 后缀，目标 100 根。周末、休市和半日市按 XNYS 日历；后缀必须连续接到前一交易日收盘，非 ready 或无效数据视作缺口。
- 缓存足够时不请求历史；不足时只请求需要补齐的交易日。直接取单日 `K_5M`，不额外保存历史 1m 或完整 raw 响应。普通冷启动通常为两个交易日、156 根历史，已有部分缓存时减少请求。
- 复用 `HistoryAdmission` 的 7 日标的额度、预留额度与共享锁/pacing；额度未知或受限时跳过。单日请求有限重试，截止约束涵盖额度查询、历史请求和 SQLite 写入。请求过程不持有 SQLite 事务。
- 单股 provider/存储故障隔离；保留其他股票已完成的历史，不让预热失败阻断正常盘中采集。每个交易日的审计保存在 `collector_meta` 的 `intraday_history_prewarm:<date>`，含 cache_hit/requested/completed/skipped/failed 与逐股原因。
- 既有 5m refs 自动覆盖补入历史，backend hydration/enrich 接续计算 MACD H、ΔH、Δ²H。保留 100 根策略门槛，不掺入盘前盘后，不额外补 21 日 RVOL 历史。

## 代码位置

- API 类型桥接：`backend/app/schemas/intraday_pool.py`、`backend/app/repositories/intraday_pool.py`。
- collector 作用域：`urus-moomoo/src/urus_moomoo/intraday_pool.py`。
- 维护、连续性与审计：`urus-moomoo/src/urus_moomoo/runtime.py`。
- 有界 K_5M 请求：`urus-moomoo/src/urus_moomoo/integrations/moomoo.py`。
- 缓存读取与写入截止保护：`urus-moomoo/src/urus_moomoo/storage/equity_history.py`。

## 验收证据

先写失败用例再实现。类型桥接的 3 个测试先因缺字段失败，之后 API pool 18 项、collector pool 13 项通过。预热主路径及边界用例见 `test_equity_maintenance_runtime.py`；Luna 独立验收见 [测试报告](intraday-equity-history-prewarm-test-report-20261020.md)。

- collector 全套 **194 项通过**，包含独立验收 3 项。
- backend 全套收集 **579 项**。首轮 578 项通过，1 项多进程 worker 用例受临时测试启动脚本缺少 `__main__` 保护影响而失败；仅修正临时启动脚本后，该 worker 模块 **5 项全部通过**。业务代码无需为该失败修改，没有剩余失败。
- backend 测试通过临时 socket guard 禁止非 loopback 外连。日志：`/private/tmp/urus-prewarm-backend-regression.log`、`/private/tmp/urus-prewarm-worker-regression.log`。
- 独立验收走真实 SQLite → refs → backend resolver/enrich；首根当前日 bar 的 H、ΔH、Δ²H 与独立 EMA/差分结果一致。第二次运行不新增 provider 请求或历史 revision；单股失败保留另一股票成功结果。
- 测试样本：1 只股票、156 条历史 5m revision，临时 SQLite 共 249,856 bytes。这是含表/索引/审计的 fixture 整库大小，不外推为生产每股增量。
- `git diff --check` 通过。只有既有依赖弃用警告。

## 观察效果与限制

部署且盘前补齐成功后，预期当天第一根完整 5m K 线被采集并汇总时，三个 MACD 值即可接续历史显示，无需从当天零根累积。真实停牌、历史缺口、额度不足等仍会保持预热/不可用状态并记录原因；不保证每只股票都能补齐。

本轮没有查询真实剩余额度或进行真实 provider 请求，线上效果及耗时需部署后的首个盘前维护记录验证。旧冻结计划缺类型时跳过预热，正常盘中采集不受影响。Session VWAP、开盘区间和 RVOL 的数据要求不因本次预热改变。
