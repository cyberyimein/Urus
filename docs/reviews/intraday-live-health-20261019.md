# 开盘后采集只读检查（2026-10-19）

检查时间：美东约 09:43–09:46（东京 22:43–22:46）。全程只读日志、已存数据库和 GET API；未部署、重启、停用任务、改配置或主动请求 OpenD 行情。

## 判断

采集器进程与调度仍推进，Eoflora 期权采集正常入库，Urus 日内股票采集处于部分失败。不能宣称整体完全正常；当前故障没有阻塞后续组或形成同槽无限重试。

## 直接证据

- Eoflora SPY／QQQ／IWM 三轮：09:35:13、09:40:11、09:45:08 完成；均 succeeded、complete=true、三个标的全部完成。前两轮 attempt_number=1，无 error_message。
- Eoflora 期权报价新鲜度另有不足：09:35／09:40／09:45 fresh_quote_coverage 约19.3%／61.0%／54.7%，低于记录的80%目标；09:45 ready232、stale179、zero_quote12、invalid_quote1。结构完整不代表所有期权报价实时可用。不能用陈旧／无效报价作实时交易信号。
- Urus G1 两轮（cutoff09:35、09:40）在 `EquityHistoryStore.write_quote_batch` 报错：`ValueError: Out of range float values are not JSON compliant: nan`。调用链为 runtime `_compact_equity_shard_payload` → quote sort `_canonical_json` → `json.dumps(...allow_nan=False)`。
- G1 一次请求全46股报价，后续规范化存储未清理非有限浮点数。现有日志不能证明哪个代码、哪个字段产生NaN，不能归因于ETF不受支持或分钟线请求失败。
- G1 的分钟分组恰好为15个ETF：QQQ、SPY、IWM、DIA、RSP、SMH、SOXX、IGV、HYG、LQD、TLT、IEF、UUP、GLD、USO。报价持久化异常会使整个G1返回storage_error、无引用；即使分钟行先写入，也不进入最终可读引用。
- 后续 G2／G3 两轮仍按时完成：09:37:14／09:38:13，09:42:11／09:43:10；本地整槽09:39:03、09:44:00也完成。没有把G1错误扩散为后续任务异常。
- 最新可读 cutoff09:40：46股请求、31股有分钟数据；预期92根5m、可用62根，缺少ETF对应30根；全池报价返回0。`equity_quote_batches`初始至检查仍0行。页面总状态stale／quality partial，真实反映G1缺失，不能解释成全部股票采集停止。
- Urus API health：production、status=ok、database=ok。事件消费者在检查快照status=ok、backlog_remaining=false、last_error=null，已处理首个部分槽；新槽有正常消费轮询延迟，不等于卡死。
- 开盘前维护已于08:00:07完成，成功日期2026-10-19。未占用盘中窗口。

## 为何不会无限重试

runtime 捕获持久化异常后，将该分片保存为失败状态并返回 `completed=True, terminal=True`；scheduler ledger记录本窗口已尝试。下一次G1会在下一个5分钟窗口执行，并非同一个失败槽紧密重跑。两个已观察窗口每次G1尝试约12–15秒，均未跨至G2窗口。Eoflora先执行，后面的G2/G3仍继续。

这表示有限的请求／日志浪费，而不是零影响；也不保证其他未来错误路径永远不会阻塞。判断范围仅为本次NaN故障与已观察开盘窗口。

## 业务影响与建议

- 31只个股的有效分钟线继续积累；MA5/MACD等仍需满足历史预热，暂时不可用不全是故障。
- ETF／大盘K线缺失，依赖SPY的相对强弱和CTA代理不可用；全池报价丢失也影响个股实时报价显示。不是“仅ETF显示失败，其余所有指标都正常”。
- 暂无证据需要在交易时段停掉整个collector。简单移除ETF未必解决，因为NaN来自全池报价中的未知字段／代码。
- 收盘后做最小修复：入库前对报价非有限值作可追溯空值规范化，必要字段无效则标记该代码不可用；报价失败与已有效的分钟引用隔离，避免报价一处错误吞掉整组分钟数据。先补NaN／Infinity和分钟引用保留的回归测试，再修复、验收。当前检查未修改业务代码或部署。

本地只读原始证据：`/private/tmp/urus-livecheck-20261019/`（collector记录、API overview／health、Eof期权quality与API日志）。
