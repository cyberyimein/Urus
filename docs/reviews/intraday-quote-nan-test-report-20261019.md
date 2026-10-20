# 日内股票报价非有限值独立验收（2026-10-19）

本轮只验收 G1 报价异常与窗口运行时之间的边界。测试使用真实
`CollectorRuntime`、`CollectorStore` 和临时 SQLite，provider 是测试内 fake provider；没有连接
OpenD、Moomoo、网络、SSH、容器或生产数据库，也没有修改部署配置。

## 验收范围

新增的独立测试文件为
[`test_equity_quote_nan_independent_acceptance.py`](../../urus-moomoo/tests/test_equity_quote_nan_independent_acceptance.py)，覆盖：

1. G1 全池报价包含 `NaN`、`+Infinity`、`-Infinity`，但分钟线有效时，G1 的 1m/5m bars refs 仍可解析；报价质量只能使分片为 partial。
2. `write_quote_batch` 抛出存储异常时，已经写入的有效 bars refs 仍保留；finalizer 必须保持 `completed=false`，不能把缺报价的槽发布成完整槽。
3. 生产形状更常见的情况：所有报价的 `last_price` 有效，只有可选/嵌套字段含
   `NaN`、`+Infinity`、`-Infinity`；G1、G2、G3 和股票槽 finalizer 仍形成完整槽，3/3
   fixture 报价以及 bars refs 均可解析，严格 JSON 编码成功。
4. G1 报价异常后，G2、G3 仍然执行；T+240 股票槽 finalizer 不再发 provider 请求，仍能生成 terminal 的 partial 槽。

## 修复前的红灯证据

在修复前，使用与新增测试相同的 P2 fake provider 形状运行 G1：每个报价行的
`last_price=NaN`，并在嵌套 `market_depth` 中放入 `+/-Infinity`。运行时在
`runtime.py:704` 调用 `write_quote_batch`，排序阶段进入
`equity_history.py:567` 的严格 JSON 编码，得到：

```text
ValueError: Out of range float values are not JSON compliant: -inf
```

该异常被整个分片规范化异常路径接管，G1 合约的 `equity_data_refs` 为空；同一次
运行的 SQLite 中仍有 6 条 bars revision（5 条 1m 加 1 条 5m），说明有效 bars 已写入，
但没有被发布为可消费引用。quote batch 写入异常的同样复现结果是 G1 refs 为空，finalizer
只能看到 G2/G3 refs。

## 当前实现与绿灯证据

当前边界保持严格 JSON：`storage/equity_history.py` 的
`_normalize_quote_row` 递归把报价中的非有限浮点值转换为 JSON `null`，并记录字段路径；
`_canonical_json` 继续使用 `allow_nan=False`。runtime 另外校验 `last_price`，只将该报价标记
为不可用，不让它污染有效报价或分钟线。quote batch 写入异常在 G1 局部处理，保留已完成的
bar refs，并将分片标为 partial；finalizer 看到缺报价时不使用旧槽补齐。

目标测试：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=/private/tmp/urus-predeploy-20261016/siteguard:$PWD/src:$PWD/tests:$PWD/../backend \
  APP_ENV=test MOOMOO_ENABLED=false FRED_ENABLED=false YAHOO_ENABLED=false \
  ANOMALO_WORKFLOW_ENABLED=false INTRADAY_EQUITIES_ENABLED=false \
  INTRADAY_EVENTS_ENABLED=false SYSTEMATIC_RISK_ENABLED=false \
  DATABASE_URL=/private/tmp/urus-quote-nan-target-20261019/db/fallback.sqlite \
  SIDECAR_CAPTURE_DATABASE_PATH=/private/tmp/urus-quote-nan-target-20261019/db/missing.sqlite \
  NETWORK_GUARD_MARKER=/private/tmp/urus-quote-nan-target-20261019/network.log \
  ../backend/.venv/bin/pytest -q tests/test_equity_quote_nan_independent_acceptance.py
```

结果：

```text
4 passed in 0.29s
```

证据日志：`/private/tmp/urus-quote-nan-target-20261019.log`。

四条用例的具体结果包括：G1 bars refs 全部可解析；有限价格加嵌套非有限字段的完整
G1/G2/G3/finalizer 槽中，quote ref、1m/5m refs 都可解析，backend hydrate 也能读出 3 条
报价和 15 条分钟线，所有报价行均可用 `json.dumps(..., allow_nan=False)` 编码；quote
storage failure 后 G1、G2、G3 均有正确的分片结果，股票槽 finalizer 为 partial 且
`completed=false`；finalizer 没有增加 fake provider 调用。

## collector 离线回归

在同一 network guard 和临时配置下运行 `urus-moomoo/tests` 全部测试：

```text
cd /Users/waynewong/code/Urus/urus-moomoo
PYTHONPATH=/private/tmp/urus-predeploy-20261016/siteguard:$PWD/src:$PWD/tests:$PWD/../backend \
  APP_ENV=test MOOMOO_ENABLED=false FRED_ENABLED=false YAHOO_ENABLED=false \
  ANOMALO_WORKFLOW_ENABLED=false INTRADAY_EQUITIES_ENABLED=false \
  INTRADAY_EVENTS_ENABLED=false SYSTEMATIC_RISK_ENABLED=false \
  DATABASE_URL=/private/tmp/urus-quote-nan-full-20261019/db/fallback.sqlite \
  SIDECAR_CAPTURE_DATABASE_PATH=/private/tmp/urus-quote-nan-full-20261019/db/missing.sqlite \
  NETWORK_GUARD_MARKER=/private/tmp/urus-quote-nan-full-20261019/network.log \
  ../backend/.venv/bin/pytest -q tests
```

结果：

```text
168 passed in 5.83s
```

证据日志：`/private/tmp/urus-quote-nan-full-20261019-final.log`。

`/private/tmp/urus-quote-nan-full-20261019-final/network.log` 为空，本轮没有观察到网络连接尝试。
全量结果包含新增独立测试、已有 quote normalization 测试、窗口 runtime/store、归一化 refs
以及其已有 backend P3/P4 bridge 覆盖。已有 Eoflora/scheduler 优先级测试也在这次全量中
通过；本新增文件没有把股票 finalizer 误称为 Eoflora capture，也没有声称验证真实 Eoflora
provider 调用链。

最终源码身份：

```text
runtime.py:         5284d69a92e68a4d7678bbb2f8e90c7814aa83c6981c3256546044a69ac47d6f
equity_history.py:  89e1f9f96d2f55711418b461951945c8f086c76825dfffbcdff31b278eb5b018
```

## 限制

本报告证明的是 fake provider 和临时 SQLite 下的运行时边界，不证明真实 Moomoo/OpenD
会返回何种报价字段，也没有在生产库、运行容器或部署环境执行采集。发布前仍需保留
`last_price` 缺失/非法导致 partial 的可见状态，并确认后续 UI 使用 `quality_summary` 与
`slot_shard_status` 的一致语义。
