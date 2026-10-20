# 日内相关未提交功能独立回归与提交准备（2026-10-20）

本报告只记录本地独立验收与静态审查。测试使用临时 SQLite、fake provider 或纯本地 fixture，未连接生产 API、OpenD、SSH、容器或生产数据库；本轮没有执行 git add/commit。

## 启动方式与网络隔离

后端测试使用仓库自己的 Python 3.12 虚拟环境：

```text
cd /Users/waynewong/code/Urus/backend
.venv/bin/pytest -q <定向测试文件>
```

需要覆盖多进程或完整套件时，使用带 `if __name__ == "__main__"` 的临时启动器
`/private/tmp/urus-prewarm-backend-regression.py`，并通过环境变量关闭真实 provider、外部工作流和三个可选日内/风险开关。此前完整回归使用的启动器会拦截非 loopback 的 `socket.connect`/`connect_ex`；严格版本的
`/private/tmp/urus-predeploy-20261016/siteguard/sitecustomize.py` 还拦截
`socket.create_connection`。临时数据库路径必须显式指向 `/private/tmp`，不能读取本机 `.env`。

collector 测试从 `urus-moomoo` 目录使用 `../backend/.venv/bin/pytest`，并将
`/private/tmp/urus-predeploy-20261016/siteguard`、collector `src` 与 backend 加入
`PYTHONPATH`。本轮定向命令继承该严格 guard，未产生外连记录。

## 静态审查结论

审查了新增的 P3 盘中投影、P4 事件评估/消费者、P5 结构策略、P6 systematic risk、P7 replay/outcomes，以及 refs/history enrichment 与 API 路由和开关接线。

- P3 有最新 partial 优先、不回退旧 complete、尾部缺口/未来 bar/非 5 分钟/非法 OHLCV fail-closed、MA5/ATR/MACD/RS/RVOL 预热与时间边界检查。
- P4 有 ATR 前置缓冲、价格急跌与放量阈值、minute boundary quality gate、S1 两槽确认、S2/S3 生命周期、TTL/收盘失效/冷却、跨日与跨 task 隔离、重复 slot、迟到 revision、并发锁、事务回滚、cursor 分页、source 错误和只读 API 检查。
- P6 只读读取既有冻结 dataset/chart；检查上一真实 session、日线缺口、创建时间/quote 时序、content hash、OHLCV、raw price basis、quote 新鲜度、partial 短模型、ETF 局部 stale、baseline/scenario 身份与默认关闭；缺失/坏主库有结构化返回并避免只读 GET 建库。
- 新 router 已接入 `backend/app/api/router.py`，开关默认关闭；新增 event/systematic-risk 表由既有模型注册，测试明确没有风险表或 provider 写入副作用。

在静态审查和定向回归中没有发现可复现的 P1/P2 缺陷。Starlette/httpx 的弃用提示仍存在，但不影响本轮行为结果。

## 定向结果

### Backend

收集并运行了以下 11 个新功能相关测试文件，共 **206 项**，全部通过：

```text
tests/test_intraday_equities_acceptance.py             39
tests/test_intraday_equity_references.py              12
tests/test_intraday_event_retention.py                 2
tests/test_intraday_event_strategy_filters.py          6
tests/test_intraday_event_supervisor.py                3
tests/test_intraday_events_acceptance.py              49
tests/test_intraday_history_features.py                6
tests/test_intraday_replay_acceptance.py              24
tests/test_intraday_structure_acceptance.py           15
tests/test_systematic_risk_acceptance.py              24
tests/test_systematic_risk_service_acceptance.py      26
```

运行使用 `APP_ENV=test`、临时 `DATABASE_URL`/sidecar 路径和
`MOOMOO_ENABLED=false FRED_ENABLED=false YAHOO_ENABLED=false`，并显式关闭
`INTRADAY_EQUITIES_ENABLED`、`INTRADAY_EVENTS_ENABLED`、`SYSTEMATIC_RISK_ENABLED`。

### Collector

运行窗口、维护、历史预热、refs、normalized storage、quote NaN、pool、quota、调度和
Eof 恢复相关的定向集合，共 **157 passed in 6.25s**。命令使用临时 SQLite 与严格
sitecustomize network guard，provider 关闭；没有启动采集服务。

### Frontend / 文件完整性

```text
npm --prefix frontend run build
```

通过 `vue-tsc --noEmit` 与 Vite 构建（88 modules），当前产物为
`index-CQFXFf8n.js` 和 `index-DWN1bHP7.css`。本轮未新增 UI 单元测试，既有
Playwright 证据沿用各专项报告。`git diff --check` 通过。

## 最终工作树全套回归

在 root 完成临时源码拆分、确认当前工作树源码身份未变后，本轮重新执行了完整离线套件。
两个套件都使用 `/private/tmp/urus-predeploy-20261016/siteguard/sitecustomize.py`，并在
backend 端通过带 `if __name__ == "__main__"` 的
`/private/tmp/urus-prewarm-backend-regression.py` 启动 pytest；所有数据库路径均为本轮
新建的 `/private/tmp/urus-commit-full-20261020/` 临时目录。

| 套件 | 结果 | 日志 |
| --- | --- | --- |
| backend | **579 collected，全部通过**；输出到 `[100%]`，仅有下列 warning | `/private/tmp/urus-commit-full-20261020/logs/backend-full.log` |
| `urus-moomoo` collector | **194 passed in 6.14s** | `/private/tmp/urus-commit-full-20261020/logs/collector-full.log` |

network guard marker `/private/tmp/urus-commit-full-20261020/network.log` 为空，没有观察到
任何 socket 外连尝试。完整 backend 输出包含两类已有 warning：

- Starlette/httpx `TestClient` 弃用提示；
- Python 3.12 sqlite3 默认 datetime adapter 弃用提示（出现在 migration fixture）。

这两类 warning 没有失败或改变测试结果。

## 最终双图 Playwright 验收

复用了既有双 pane fixture 和脚本
`output/playwright/urus-intraday-macd-overlay-20261020/overlay_macd_acceptance.mjs`，
本轮 Vite 只监听 `127.0.0.1:5192`，所有 `/api/*` 由 Playwright route fixture 返回。

第一次在默认 sandbox 中启动系统 Chrome 时浏览器进程 SIGABRT，未产生任何 API 请求，也未
进入页面断言；这是浏览器启动权限问题，不是应用或 fixture 断言失败。按 Playwright 本机
浏览器权限重试后通过：

- `passed=true`，5 个 fixture 状态共 **25 个 GET**；
- `unmatchedApi=[]`、`consoleErrors=[]`、`pageErrors=[]`；
- H 柱、ΔH/Δ²H 叠加曲线、marker、缺口/空数据和 390px 无横溢出均通过；
- 1440px MACD 卡片尺寸约 `918 × 507.78` CSS px；
- 证据 JSON：`/private/tmp/urus-commit-full-20261020/logs/macd-overlay-playwright-escalated.json`；
- 截图：`/private/tmp/urus-commit-full-20261020/playwright-artifacts-escalated/`。

测试完成后已停止本轮启动的 Vite，`127.0.0.1:5192` 无监听进程。

## 限制与提交提示

本地 fake provider 不能证明真实 Moomoo/OpenD 字段、额度或生产耗时；本报告没有查询真实
额度或触发真实采集。现网 health 中历史的 readonly 错误应继续按部署报告记录，不能被
本地测试通过结果覆盖。full suite 通过只说明当前工作树在隔离环境中可回归，最终拆分
commit 仍应由 root 保存对应源码 SHA、测试日志和变更边界。
