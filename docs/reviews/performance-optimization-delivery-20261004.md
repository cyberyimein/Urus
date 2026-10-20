# Urus 性能优化实施结果（2026-10-04）

按 [性能优化实施交接文档](/Users/waynewong/code/Urus/docs/reviews/performance-optimization-handoff-20261004.md) 完成六项基础优化。PERF-02、PERF-03 实施文档的第一阶段；批量快照加载及跨请求完成结果缓存仍作为后续评估项。修改保留在工作区、尚未提交；后端性能补丁和前端构建已于 2026-10-04 部署，见 [部署验收](performance-optimization-deployment-20261004.md)。没有新增 migration，没有修改本地历史数据库。

后续代码审查发现的两项 P2 已修复：空决策策略集清理旧图形，非规范 manifest 回退到原 reader。新增 13 个回归用例后，后端全量 **315 个测试通过**；回放复测中位数 **0.383 秒**，历史输出仍一致。修复细节及验收见 [性能优化代码审查](/Users/waynewong/code/Urus/docs/reviews/performance-optimization-code-review-20261004.md)。下文保留首次实施的测量记录。

## 1. 实施范围

| 项目 | 实施状态 | 实现与保留边界 |
| --- | --- | --- |
| PERF-01 单结果查询限量 | 已完成 | 当前/前序快照、按 scope/date 取 Dataset、前序 Dataset、最新 synthesis 均加 SQL `LIMIT 1`；保留过滤、排序和无结果语义。 |
| PERF-02 Discovery 上下文复用 | 第一阶段完成 | `CrossSectionContext` 显式传递；一次 Discovery 请求仅加载一次当前及前序快照集合，8 个指标复用上下文。质量错误仍按原指标输出；上下文仅在请求内保留。 |
| PERF-03 回放复用 | 第一阶段完成 | 二分定位历史前缀，单项缓存复用相同前缀的 scan/setup；精确相同 symbol/cutoff 的已校验窗口直接交给 scanner。旧数据缺 manifest 或需要重新裁剪时使用原 reader。 |
| PERF-04 单标的图表/最小回放证据 | 已完成 | SQL JSON 路径投影，不构建完整 Chart ORM；历史页面一次请求读取选中图表和同一策略集的决策；单标的 worker 只读取 bars/quality/asset_type。 |
| PERF-05 批量 canonical bar 入库 | 已完成 | 完整唯一身份去重，100 个 key 一批查重和插入；SQLite/PostgreSQL 对准确唯一键使用 `ON CONFLICT DO NOTHING`；按原输入顺序返回，包括同批重复项。 |
| PERF-06 Run 摘要/按需详情 | 已完成 | 新增独立摘要 API，显式 select 元数据及 JSON 标量；三个页面迁移。Run 报告按选择加载，切换清除旧报告，取消旧请求并拒绝晚到响应。 |

本次未改变策略算法、首次触发规则、生命周期、交易日历或证据可见性条件。保留单标的机会范围校验、single-flight、worker 并发上限、超时及取消回收。没有引入跨请求大对象缓存。

## 2. 主要实现位置与接口契约

### 查询与 Discovery

- [observation.py](/Users/waynewong/code/Urus/backend/app/repositories/observation.py)：快照限量、`list_run_summaries()`。
- [daily_evidence.py](/Users/waynewong/code/Urus/backend/app/repositories/daily_evidence.py)：Dataset 限量、批量 bars、`chart_instrument()`。
- [strategy_decisions.py](/Users/waynewong/code/Urus/backend/app/repositories/strategy_decisions.py)：最新 synthesis 限量，`bundle(symbol=...)` 保留 synthesis 选择后对 strategy set 的绑定。
- [cross_section.py](/Users/waynewong/code/Urus/backend/app/decision_harness/cross_section.py)、[discovery.py](/Users/waynewong/code/Urus/backend/app/decision_harness/discovery.py)：分开加载上下文与指标构建，原单指标入口继续可用。

Discovery 的组阶段事件暂时保留原读取逻辑；实测已达到文档第一阶段 181→31 次 SQL 的目标。剩余查询仍随组数量增长，尚未实现窗口函数/批量前序快照读取。

### Run 摘要

新增 `GET /api/observation/run-summaries?limit=30`，静态路由在动态 Run 路由之前注册。独立 `ObservationRunSummaryResponse` 包含身份、状态、日期、universe freshness、组数量、质量问题数量、创建/完成时间和错误摘要，不包含 report/options/alignment。

Repository 不加载 Run ORM；successful count 兼容旧 payload 中未写入显式 count 的情况。原 `/observation/runs`、Run detail 和 create API 保持兼容。

Discovery、CrossSection 使用摘要；ObservationRunView 先读摘要，再调用 `getObservationRun(runId, signal)`。列表和详情都有请求代次检查；页面卸载后晚到的列表不会触发新详情请求。

### 单标的证据

新增 `GET /api/daily-evidence/datasets/{dataset_id}/instruments/{symbol}/evidence`，使用 `DailyEvidenceResponse` 外层结构，一次返回：

- 原冻结 Dataset；不改变其 ID、manifest 或 hash。
- 只含所选标的的图表，以及适用于该标的/共享的 overlays、state segments、events。
- 所选标的的策略决策和对应完整 synthesis。一次选定 synthesis/strategy set 后取决策，避免多次请求中策略集合漂移。

子图表使用独立 `urus.instrument_chart_projection.v1` schema，带 `symbol`、`parent_stored_content_sha256`、`strategy_set_sha256`，重新计算子图表的 `content_sha256`。父 hash 表示存储的完整图表，不代表当前裁剪/叠加后内容的 hash。

证据读取允许 Dataset scope 中的 primary、benchmark、auxiliary symbol；机会视图继续只允许 primary scope symbol，未扩大机会范围。非法 symbol 为 422，Dataset/图表不存在为 404。数据库 JSON 路径和前端 URL 均通过参数/编码处理。

历史 InstrumentDecisionView 改用单次原子证据请求；移除整份多标的 `frozenEvidence` 引用。切换 symbol/Dataset/来源上下文时释放旧图表，并保留 AbortController、身份与请求代次检查。读取仍不采集、不写入、不生成新策略。

worker 的 `get_replay_chart(dataset_id, symbol)` 只读取 bars、quality、asset_type，保留旧图表缺标的时的 unavailable 行为。显式 `symbol=None` 的全标的研究调用保持旧路径；不能将单标的收益推广到全标的请求。

### 批量入库与回放

bar identity 仍为 `symbol/exchange/bar_date/adjustment/source/source_revision`，revision hash 沿用原算法。先规范化所有输入，再在同一事务内处理；不分批 commit。相同 revision 返回原 ID/采集时间，不覆盖旧价格；新 revision 追加，cutoff 可见性不变。批内重复输入仍保持返回条数、顺序和对象身份。

回放缓存仅保留一个前缀结果，避免保存所有日期的 FrozenEvidence。`_ReplayEvidenceReader` 只在已构建/校验的窗口与 scanner 请求完全相同时复用；legacy manifest、warnings 去重或不同 cutoff 走原 reader。本次没有跳过 FrozenEvidence 校验，也没有给 bar hash 加可能失效的全局缓存。

## 3. 本地前后测量

环境：macOS 27.0.1 arm64，Python 3.12.13，SQLite 3.50.4，SQLAlchemy 2.0.51。基线 HEAD 为 `d62c62d245936943b6990f4fe2f7eea409c8eb54`；工作区含此前修改。旧算法比较使用实施前保存输出；bars 入库基线从该 HEAD 读取旧 Repository，在独立内存库执行。

历史库 `backend/urus.db` 约 839 MiB，仅用 SQLite `mode=ro` 读取。该库迁移停留在 `0024_remote_decision_workflows`，最新 Run 为 2026-08-24；没有升级原库。以下均为本机函数/序列化/隔离进程测量，不是线上 P50/P95、网络耗时或容量承诺。

| 场景与测量边界 | 优化前 | 优化后 | 说明 |
| --- | ---: | ---: | --- |
| 10 组 Discovery SQL 次数 | 181 | 31 | 同一 Run，完整输出一致。 |
| Discovery 预热耗时，5 次中位数 | 92.33 ms | 24.50 ms | 函数内部，不含 HTTP/浏览器。 |
| 5 条 Run 列表 JSON | 596,662 B | 2,132 B | `json.dumps(default=str)` UTF-8；详情列表与新摘要列表比较。 |
| SPY 366 bars 机会视图预热耗时，3 次中位数 | 1.872 s | 0.376 s | 已加载输入及预热日历；不含 spawn/数据库。约减少 80%。 |
| 完整/选中证据外层 JSON | 11,409,537 B | 1,114,791 B | 包含 Dataset、chart、decisions、synthesis。约减少 90.2%。 |
| 单标的回放读取的图表 JSON | 10,255,967 B | 76,490 B | 28 标的有效图表→SPY 最小 bars/quality 证据；不含 Dataset。 |
| 隔离进程图表读取耗时，3 次中位数 | 110.40 ms | 46.58 ms | 相同优化后回放算法，单独比较完整读取与最小读取。 |
| 隔离进程读取后峰值 RSS，3 次中位数 | 194.72 MiB | 156.44 MiB | 两种路径进程导入基线均约 69 MiB。 |
| 同进程读取并回放后峰值 RSS，3 次中位数 | 248.47 MiB | 212.08 MiB | 峰值约减少 14.6%；不是整个旧/新 worker 算法的综合内存对照。 |

证据样本 Dataset 为 `5bcad7ee-aeee-4018-8592-3c1947df1a78`，symbol 为 SPY，固定 `viewed_at=2026-10-04T00:00:00Z`。选中图表本身为 387,069 B，原冻结 Dataset 仍为 707,830 B；新证据响应约 1.1 MB，不能宣称整个 API 只有约 76 KB。

回放耗时原始值：旧 `[1.862832, 1.914064, 1.871776]` 秒，新 `[0.377203, 0.376280, 0.361977]` 秒。RSS 三次范围：完整读取 194.42–194.75 MiB，最小读取 156.42–156.48 MiB；读取并回放完整 248.36–248.48 MiB、最小 212.08–212.17 MiB。这里使用 macOS `resource.ru_maxrss` 的字节单位换算，不混用 Linux KiB。

SQLite JSON 路径投影仍需在数据库内部解析原 JSON；它减少进入 Python 的内容和 ORM 构建，不意味着存储 I/O 缩小同样比例。没有测量数据库实际读盘字节、浏览器 heap 或帧率。

真实 coordinator 连续执行 5 次单标的 spawn/读取/回放后，父进程累计峰值 MiB 为 `[52.30, 52.78, 52.97, 52.97, 52.98, 53.00]`；最后无活动子进程。这是有限次数的本地资源回收检查，不是长期在线内存稳定性保证。

### bars 入库对照

统一使用生产 `autoflush=False`、相同 ORM 模型、独立内存 SQLite 库及 `before_cursor_execute` 计数。时间包含方法执行及 flush，不含生成 fixture；每格为一次执行，不视为统计分位数。

| 输入 | 旧首次 | 新首次 | 旧重复 | 新重复 |
| --- | --- | --- | --- | --- |
| 520 根 | 65.0 ms；SELECT 520 / INSERT 1 | 20.8 ms；SELECT 12 / INSERT 6 | 45.0 ms；SELECT 520 | 13.2 ms；SELECT 6 |
| 100 symbols × 520 根 | 5.355 s；SELECT 52,000 / INSERT 1 | 1.517 s；SELECT 1,040 / INSERT 520 | 4.269 s；SELECT 52,000 | 0.998 s；SELECT 520 |

INSERT 计数表示 cursor execution 批次（含 executemany），不是逐行物理写入次数。旧生产路径本就由 ORM 批量 INSERT；新路径写入调用数更多，但去除了逐 bar 查重并降低整体耗时。原交接文档默认 autoflush 实验已在原位更正。

## 4. 等价性与验证

- 本地全部 5 个 Run 的完整 Discovery 输出与实施前保存结果一致，包括排序、警告、事件和 hash。
- 70/260 bars × `include_scan_manifests=False/True` 四组完整 replay 序列化输出与基线一致；SPY 历史机会视图与基线一致。
- 最小图表读取与完整图表读取构建的 SPY 机会视图一致；三个独立进程对照的结果摘要相同。
- 新回归覆盖单结果 ORM 只加载一条、有前序历史的上下文复用、摘要不加载 Run ORM、大型无关标的不进入 Chart ORM、批内重复、revision/采集时间保留、历史 cutoff、事务 rollback、非法输入拒绝。
- reader 覆盖正常/partial/failed 与 legacy manifest、相同/更早 cutoff；回放覆盖前缀只扫描一次及原 reader 对照。
- 前端覆盖 Run/标的快速切换、旧响应晚到、回到原标的并更换 Dataset、清空旧显示、卸载取消、卸载后列表返回不启动详情。
- worker 覆盖真实 spawn 和只读 SQLite、正常退出、超时回收、取消回收、single-flight、不同 key 容量拒绝、一个等待者离开时保留共享工作、最后等待者离开后释放容量。

最终验证结果：

- Backend：`.venv/bin/python -m pytest`，302 个测试通过。
- Frontend：`npm test`，16 个文件、88 个测试通过。
- Production build：`npm run build`，TypeScript 检查和 Vite 构建通过；JS 310.10 kB / gzip 102.58 kB，CSS 129.86 kB / gzip 20.73 kB。
- `git diff --check` 通过。

新增后端测试位于 [test_performance_queries.py](/Users/waynewong/code/Urus/backend/tests/test_performance_queries.py)、[test_performance_evidence.py](/Users/waynewong/code/Urus/backend/tests/test_performance_evidence.py)、[test_opportunity_replay_worker.py](/Users/waynewong/code/Urus/backend/tests/test_opportunity_replay_worker.py)；回放和前端测试补充在原测试文件中。测试不依赖本地历史库，也不按固定毫秒阈值断言。

## 5. 复现与后续事项

在 `/Users/waynewong/code/Urus/backend` 执行全量后端测试，在 `/Users/waynewong/code/Urus/frontend` 执行前端测试/构建。原交接文档中的 Discovery 只读测量入口仍可使用；当前实现应输出约 31 次 SQL，样本时间可能随机器负载变化。

复现只读的最小/完整图表及固定机会视图，可从仓库根目录运行以下 Python 片段（`PYTHONPATH=backend backend/.venv/bin/python`）。比较 RSS 时两种模式分别在新进程执行，并确保历史库文件已存在；不得为测量创建或迁移该库：

```python
from datetime import UTC, datetime
import resource
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.decision_harness.market_evidence import DailyMarketEvidenceService
from app.services.opportunity_views import build_dataset_opportunity_view

mode = "minimal"  # 另开进程改为 "full"
engine = create_engine("sqlite:///file:backend/urus.db?mode=ro&uri=true")
with Session(engine, autoflush=False) as session:
    daily = DailyMarketEvidenceService(session, Settings())
    dataset_id = "5bcad7ee-aeee-4018-8592-3c1947df1a78"
    dataset = daily.get_dataset(dataset_id)
    assert dataset is not None
    chart = (daily.get_replay_chart(dataset_id, "SPY")
             if mode == "minimal" else daily.get_chart(dataset_id))
    payload = build_dataset_opportunity_view(
        dataset, chart, viewed_at=datetime(2026, 10, 4, tzinfo=UTC), symbol="SPY",
    )
    print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2)
engine.dispose()
```

仍需后续评估/验证：

1. Discovery 大组数时的批量当前/前序快照读取；本次仅完成请求内复用，不加入额外索引或 migration。
2. 回放完成结果缓存：目前单次函数 CPU 已明显下降，先测实际访问频率/P95，再决定是否增加紧凑、有界、完整身份的缓存。
3. PostgreSQL 真实数据库集成。代码包含对应 JSON/INSERT 方言处理，但本次仅在 SQLite 执行功能测试，没有声称已通过 PostgreSQL 在线验证。
4. 线上 HTTP 压缩后流量、冷启动 spawn 开销、并发 P95、浏览器渲染及长期父进程 RSS。本次本地测量不足以替代这些部署指标。

工作区原有 CrossSection UI、Apple moomoo 部署及 sidecar 修改已保留；CrossSection 中本次性能修改仅为摘要 API/类型/质量计数消费及对应测试迁移。检查 diff 时应区分这些先前修改，本次不替其他任务声明测试或部署结果。
