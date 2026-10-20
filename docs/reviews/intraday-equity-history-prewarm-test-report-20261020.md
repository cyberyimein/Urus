# 盘前股票 5 分钟历史预热独立验收（2026-10-20）

本轮验收只覆盖新增的最小范围：盘前按明确股票作用域预热连续 100 根历史 5 分钟 bar，缓存命中不请求 provider，预热结果能够被紧凑 refs 解析并供后端 MACD/H、ΔH、Δ²H 计算，单个股票失败不丢弃其他已成功股票。测试使用 `CollectorRuntime`、`EquityHistoryStore`、临时 SQLite 和 fake provider；没有连接 OpenD、Moomoo、网络、SSH、容器或生产数据库。

独立测试文件：

[`urus-moomoo/tests/test_equity_history_prewarm_independent_acceptance.py`](../../urus-moomoo/tests/test_equity_history_prewarm_independent_acceptance.py)

## 目标测试结果

执行命令：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
../backend/.venv/bin/python -m py_compile tests/test_equity_history_prewarm_independent_acceptance.py
../backend/.venv/bin/pytest -q tests/test_equity_history_prewarm_independent_acceptance.py
```

语法检查通过。开发补齐 helper 与 `fetch_intraday_history_5m` 后，定向测试结果为：

```text
3 passed in 0.31s
```

第一次红灯保留的实现缺口如下：

```text
NameError: name '_history_suffix_count' is not defined
urus-moomoo/src/urus_moomoo/runtime.py:1361
```

当时源码中同一预热路径还引用了 `_history_dates_to_fill`、`_regular_history_bars` 和
`_session_suffix_count`，因此 provider 的调用、缓存命中、refs 解析和 MACD 断言均未执行。该红灯只记录开发中实现缺口，不作为业务行为失败结论；当前版本已通过上述三项行为验收。

## 已验证行为

1. `prewarm_symbols` 只允许任务 `symbols` 范围内的个股；首次运行写入 156 根（两个完整 XNYS 会话），第二次运行返回 `cache_hit`，provider 请求数与行数均不增加。
2. fake provider 在请求期间记录 SQLite `connection.in_transaction`，所有请求均为 `False`，证明 provider 请求不持有源库事务。
3. 预热写入的 refs 经真实 `hydrate_capture_from_path`/`resolved_capture_data` 读取，后端首根当前 bar 的 MACD histogram、`histogram_delta`、`histogram_delta2` 与独立 EMA/差分计算一致，历史 ref 没有当前日或未来 bar。
4. BBB provider 失败时，AAA 的 156 根仍可读取，BBB 没有历史行，并留下 `intraday_history_prewarm:<trading_date>` 审计记录。

## 最小本地容量测量

使用 pytest 的临时目录重跑同一独立文件，未启动额外服务。每个用例的临时
`collector.sqlite` 均为 61 页 × 4096 bytes，即 **249,856 bytes**；每库含 **156 条
5m bar revision、1 个股票、158 条 watermarks**。本 fixture 使用 SQLite 默认 DELETE
journal，测试结束时没有 `-wal` 文件。该数字只代表本 fixture 的两会话、一个股票和预热审计/准入元数据，不外推到生产池或 21 日容量。

容量复核命令与证据目录：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
../backend/.venv/bin/pytest -q tests/test_equity_history_prewarm_independent_acceptance.py \
  --basetemp /private/tmp/urus-prewarm-independent-capacity-GxdgPr
sqlite3 /private/tmp/urus-prewarm-independent-capacity-GxdgPr/test_prewarm_scope_cache_hit_a0/collector.sqlite \
  "SELECT COUNT(*) FROM equity_bar_revisions WHERE timeframe='5m'; PRAGMA page_count; PRAGMA page_size;"
```

## 相关定向回归

为确认新增 helper/provider 没有破坏已有边界，另外运行了：

```text
../backend/.venv/bin/pytest -q tests/test_equity_maintenance_runtime.py \
  tests/test_equity_history_refs.py tests/test_intraday_equity_reference_runtime.py
```

结果为 `25 passed`。backend refs 与历史指标定向回归也通过：

```text
../backend/.venv/bin/pytest -q ../backend/tests/test_intraday_history_features.py \
  ../backend/tests/test_intraday_equity_references.py
```

该命令通过；仅出现已有的 Starlette/httpx deprecation warning，没有失败。

这些测试仍然使用 fake provider，不能证明真实 Moomoo/OpenD 返回字段、实际配额或生产运行时延；provider 适配器的真实 `K_5M` 请求仍需保持运行时准入、共享 pacing 和 deadline 约束。测试没有在生产库、运行容器或部署环境执行采集。
