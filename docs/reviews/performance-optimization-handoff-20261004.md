# Urus 性能优化实施交接（2026-10-04）

本文记录实施前的性能审查、只读测量和修改建议，供实施及后续审查参考。本文中的“当前实现”指审查时的实现。2026-10-04 已按本文完成基础优化，具体范围、前后测量、测试及保留事项见 [性能优化实施结果](/Users/waynewong/code/Urus/docs/reviews/performance-optimization-delivery-20261004.md)。未执行数据库迁移或部署。

## 1. 基线、范围与结论

- 审查基线 HEAD：`d62c62d245936943b6990f4fe2f7eea409c8eb54`；工作区存在未提交修改，实际审查对象为当时工作区，不是纯净 HEAD。
- 本地数据库：`backend/urus.db`，仅以 SQLite URI `mode=ro` 读取。文件约 839 MiB，Alembic 版本为 `0024_remote_decision_workflows`；最新 Observation Run 的交易日为 `2026-08-24`。
- 数据规模：16,880 条 canonical daily bars、129 份 Dataset、129 份 Chart Projection、30 份 Group Snapshot、5 个 Observation Run。
- 工作区中的最新迁移已超过本地数据库版本。不要为复现本文测量而升级或重写原库；新增 schema 的验证使用隔离测试库。
- 数字均为本机函数级测量、JSON 大小或内存数据库实验，不是线上 P50/P95、网络传输耗时或部署容量承诺。
- 未对线上服务、真实 OpenD 限频、全量采集周期、浏览器帧率和并发压力进行测量。

主要收益来自减少重复数据库读取、减少大型 JSON 的加载范围，以及消除机会回放中的重复证据构建。暂不以增加 worker、更换数据库、降低 OpenD 请求间隔或引入 Redis 作为第一步。

| ID | 修改项 | 优先级 / 改动风险 | 建议批次 |
| --- | --- | --- | --- |
| PERF-01 | 单结果历史查询显式限量 | 高 / 低 | 第一批 |
| PERF-02 | Discovery 复用并批量加载快照上下文 | 高 / 低至中 | 第一批 |
| PERF-03 | 机会回放复用历史前缀和确定性计算 | 高 / 中至高 | 第三批，可先独立剖析 |
| PERF-04 | 图表按标的读取，回放读取最小证据 | 高 / 中至高 | 第二批 |
| PERF-05 | 日 K 批量去重与插入 | 中 / 中 | 第二批，可独立实施 |
| PERF-06 | Observation Run 轻量列表与按需详情 | 中 / 中 | 第一批 |

## 2. 全部修改必须保留的领域约束

开始实施前阅读 [CONTEXT.md](../../CONTEXT.md) 和 [当前日线机会与回放内存修订](current-opportunity-memory-20260907.md)。

1. Dataset、历史快照和 canonical bar revision 的不可变身份不能因为性能优化而改变。
2. 严格保留 `cutoff_time`、`knowledge_at`、`available_at`、`collected_at` 和交易日历过滤；不能把后来采集或修订的价格暴露给更早的证据视图。
3. 保留 `partial`、`unavailable`、`failed`、来源冲突和历史重建的状态及警告，不能通过缓存或跳过校验把不完整证据提升为完整。
4. 保留相同组版本、feature version、benchmark 和策略 implementation identity 的比较边界。
5. 读取页面不能触发新的市场采集，也不能重写历史证据。新增派生存储应通过明确的生成流程产生。
6. 保留机会回放的子进程退出回收、并发上限、single-flight、超时和断连取消；不得引入无界任务队列或保留完整回放对象的无界缓存。
7. 前端继续消费后端策略与投影，不为了减少响应大小而在浏览器重新推导信号。
8. 任何高风险覆盖、清理、强制 Git 操作或删除，均遵守用户提供的 AGENTS.md 规则，单独取得明确授权。

## 3. PERF-01：单结果历史查询显式限量

### 已确认的问题

`ObservationRepository.latest_snapshot()` 与 `previous_snapshot()` 在排序后调用 `Session.scalar()`，但没有 SQL `LIMIT 1`。调用方只拿到一条，并不意味着数据库和 ORM 只加载一条。

隔离实验创建 100 条同组版本快照，每条 payload 中有约 200 KB 字符串；通过 SQLAlchemy `load` 事件统计对象构建数：

| 实现 | ORM 加载条数 | 函数耗时 | 返回记录 |
| --- | ---: | ---: | --- |
| 当前 `previous_snapshot()` | 100 | 16.5 ms | 第 100 条 |
| 相同查询加 `.limit(1)` | 1 | 1.1 ms | 第 100 条 |

这是一次微基准，不能把约 15 倍的差异当作页面整体加速比。

### 修改范围与实现建议

- [backend/app/repositories/observation.py](../../backend/app/repositories/observation.py)：两个方法在现有 `order_by()` 后添加 `.limit(1)`。
- 同类代码检查发现以下方法也没有 SQL 限量，建议纳入同一补丁，但它们尚未单独计时：
  - [daily_evidence.py](../../backend/app/repositories/daily_evidence.py)：`dataset_for_scope_date()`、`previous_dataset()`。
  - [strategy_decisions.py](../../backend/app/repositories/strategy_decisions.py)：`bundle()` 的 synthesis 查询，当前使用 `.scalars(...).first()`。
- 保持原来的筛选、排序和无结果时的返回值。不要在这个补丁中顺带改变“前一个可用日期”与“严格 T-1”的业务含义。
- 主键或唯一约束已保证单条的查询，不必机械地添加限量。
- 先补限量，再在代表性数据上检查查询计划。如排序仍是主要成本，可评估 `(group_id, group_version_id, trading_date, created_at)` 索引；`latest_snapshot()` 允许不指定版本，需同时检查这一分支。索引通过新 Alembic migration 添加，不修改历史 migration。

### 验证与验收

- 多日期、多创建时间、多组版本时，选中的记录与修改前一致；无结果仍返回 `None`。
- 隔离数据库中准备 100 条匹配记录，确认只构建一个 ORM 对象；统计不包含数据准备阶段，使用新 Session 避免 identity map 干扰。
- SQL 确实包含结果限量，而非先 `.all()` 再取第一项。
- 现有 observation、daily evidence、strategy 和 daily comparison 测试通过。

## 4. PERF-02：Discovery 复用快照上下文

### 已确认的问题与实验

[DiscoveryProjectionService.build()](../../backend/app/decision_harness/discovery.py) 依次调用 8 个指标的 `indicator_projection()`；每次都由 [CrossSectionService._load_contexts()](../../backend/app/decision_harness/cross_section.py) 重新读取当前及前序快照。组阶段事件生成还会读取组版本和部分快照。

本地 Run `50f77d4e-25ad-4124-934d-22d93bce1064` 包含 10 个组：

| 实验 | SQL 次数 | 3 次执行中位数 |
| --- | ---: | ---: |
| 当前 Discovery | 181 | 90.7 ms |
| 仅在请求内复用 `_load_contexts()` 结果 | 31 | 19.0 ms |

实验通过临时替换服务实例的方法完成，没有写入源文件。两种实现输出的完整 Python payload（含 digest）相等。该样本没有可比前序快照，不能替代有历史变化、版本切换等场景的验证。

### 第一阶段：一次请求只加载一次

推荐把“加载上下文”与“构建某个指标投影”拆开：

1. 引入明确的内部上下文类型，持有 Run、成功快照上下文和失败组信息。
2. 保留现有 `indicator_projection(run_id, indicator_id)` 对外入口，内部先加载，再调用纯投影函数。
3. Discovery 入口加载一次上下文，并将其传给每个指标构建函数。
4. 尽量让 `_group_phase_events()` 复用原始快照与组版本映射，减少第二轮查询；其版本、主题资格和来源验证仍保留。
5. 共享上下文只读。策略比较会使用 `replace()` 重置前序比较链，不能原地修改共享对象，影响后续指标。

不要直接将实验中的 monkeypatch 留在产品代码。若采用实例缓存，必须确保服务实例只属于一个请求，且缓存键包含 Run 身份；显式传递上下文更容易审核。

### 第二阶段：批量读取

- 当前快照：收集 Run manifest 的 snapshot IDs，使用有大小上限的 `IN` 查询，构建 ID 映射；输出仍按 manifest 的顺序与资格规则组装。
- 前序快照：可使用窗口函数或相关子查询，按每个当前上下文的 `group_id`、`group_version_id`、严格早于当前 `trading_date` 的条件，挑选排序第一条。
- 若使用窗口函数，先选候选 ID 再加载选中记录，避免把所有历史 JSON 加载到 Python 后分组。
- 组版本批量读取并映射，避免每个组一次 `session.get()`。
- 不得为了批量化取消 manifest 中的 snapshot/dataset/group version 身份检查；缺失记录与失败组仍进入原有质量输出。
- 不引入跨请求全局 ORM 缓存；它会持有 Session/大 payload，并可能造成版本污染。

### 验证与验收

- 第一阶段在上述本地样本中，应复现约 181 → 31 次查询，或更少；其他 fixture 以“每请求只加载一次快照集合”为标准，不硬编码 31。
- 第二阶段，查询次数随分块数量增长，不应随指标数量乘以组数量增长。
- 覆盖有可比前序、没有前序、feature version 变化、strategy implementation 变化、benchmark 不一致、失败/缺失组、重叠组事件去重。
- 固定输入的事件、排序、警告、身份字段和 content hash 一致。
- 主要测试位置：`backend/tests/test_observation.py`，前端补充验证 `DiscoveryView.spec.ts` 与 `CrossSectionView.spec.ts`。

## 5. PERF-03：机会回放减少重复证据构建

### 已确认的问题

主要调用链：

```text
build_dataset_opportunity_view
  → replay_daily_views
    → replay_daily_opportunities
      → 每日 _scan_candidate（部分日期另做收盘后扫描）
        → _evidence_window
        → scan_universe
          → InMemoryDailyEvidenceReader.load
            → freeze_daily_evidence
```

本地 Dataset `5bcad7ee-aeee-4018-8592-3c1947df1a78`，SPY 366 根日 K，`viewed_at=2026-10-04T00:00:00Z`：

- 直接运行视图构建函数、数据已加载且日历预热后，两次耗时为 **1.918 秒、1.985 秒**，输出为 `stale`。
- 独立 cProfile 调用耗时 4.042 秒；其中回放约 3.194 秒，607 次 `_scan_candidate()`，608 次 `freeze_daily_evidence()`，约 36.4 万次 `json.dumps()`。
- profile 数据包含剖析开销及首次导入，不与上面的未剖析时间混算。
- 上述耗时不含生产 worker 的 spawn、数据库读取、网络和前端渲染。

重复过滤历史前缀、重新构建 EvidenceBar、全量 Pydantic 校验及 hash 是明确的 CPU 热点。现有 single-flight 仅复用进行中的工作，完成后不保留结果。

### 修改范围

- `backend/app/buy_opportunities/daily_replay.py`
- `backend/app/buy_opportunities/evidence.py`
- `backend/app/buy_opportunities/contracts.py`（只有确需明确不可变契约时才改）
- `backend/app/services/opportunity_views.py`
- `backend/app/services/opportunity_view_replay.py`

### 第一阶段：回放内部复用，不增加跨请求缓存

1. 预先构建排序后的有效 bars 和日期索引，使用单调索引或二分确定前缀。保留真实交易日历、缺失 session、可见时间过滤；不能把 bar 序号当作交易日序号。
2. 收盘后扫描与下一交易日开盘前扫描可能使用相同证据前缀。复用相同输入的扫描结果，避免两次 `_scan_candidate()`。
3. 复用范围限定在单次 replay：evidence、cutoff、pool version、universe item、manifest 模式等必须固定，才可用前缀终点作为局部键。
4. 优先使用单项或有界前缀缓存。不要为每个日期保存完整 FrozenEvidence/manifest，重新引入平方级内存占用。
5. 检查共享 scan/setup 是否被后续生命周期修改；必要时使用不可变对象或复制最小可变部分，避免历史步骤被后续状态污染。
6. 在已验证证据、知识截止完全相同且没有进一步裁剪时，评估 evidence reader 的快速路径。必须证明其结果等价，且保留质量严重度合并和 warnings 去重逻辑。
7. `EvidenceBar.content_sha256` 当前是计算属性。只有证明对象字段不会再被修改，才能缓存它；不能直接加会变陈旧的 `cached_property`。不允许用全局跳过 Pydantic 校验来换速度。

### 第二阶段：可选的完成结果缓存

第一阶段和 PERF-04 完成后重新测量，再决定是否值得加入。

- 优先缓存由冻结证据决定的紧凑生命周期结果，不缓存完整 Chart、完整 ReplayResult、ORM 对象或每日日线前缀。
- 身份至少覆盖：数据库/数据源命名空间、Dataset 及输入内容 hash、symbol、实际 knowledge cutoff、回放算法版本、日历身份/版本，以及影响结果的质量和配置。
- 若缓存包含策略 overlay，还必须纳入实际 strategy set/implementation identity；Dataset ID 单独不足以代表动态叠加策略后的完整图表。
- `viewed_at`、stale/fresh、跨 session 的到期状态等时间相关字段在请求时重建；不能认为仅替换 `viewed_at` 和 hash 就适用于所有完成结果。
- 跨午夜、开盘/收盘边界、未来 cutoff、提前收市、夏令时变化均需验证。
- 缓存必须有字节上限、条目上限和淘汰机制；TTL 只解决保留时间，不代替版本身份。
- 不缓存 `replay_busy`、取消、超时或临时失败作为正常长期结果。
- 多 API 进程时必须说明缓存和 worker 上限是每进程还是全局，不声称进程内缓存可以全局去重。

### 验证与验收

- 优化前后在同一固定输入上的 scan/setup/trigger 身份、首次 trigger、生命周期、历史时间线、质量和 hash 一致。
- 验证前一日收盘形成的 setup 不能被当日更早的 bar 触发；保留重建等待 session 数和缺失 bar 行为。
- 包含正常、无 setup、partial/conflicted、历史重建、截止前不可见修订、缺失 session、触发后失效/到期、多次重建样本。
- 保留单标的请求隔离、single-flight、不同请求拒绝/容量边界、所有等待者取消后停止子进程、超时回收的行为。
- 比较未剖析耗时、worker 峰值 RSS，以及反复切换标的后的父进程 RSS；只减少 CPU 但显著增加常驻内存不算完成。
- 建议目标：在代表性 366-bar 样本上取得可重复的明显加速（可将至少 30% 作为开发目标），但这不是已经测得的优化收益，也不作为跨机器固定秒数 CI 断言。
- 测试位置：`test_daily_opportunity_replay.py`、`test_opportunity_view.py`、`test_opportunity_view_api.py`、`test_buy_opportunities.py` 及前端 `OpportunityViewPanel.spec.ts`。

## 6. PERF-04：按标的读取图表与回放证据

### 已确认的问题

`InstrumentDecisionView.loadEvidence()` 首次加载历史 Dataset 时调用完整 `getDailyChart()`。worker 即使只回放一个 symbol，也调用 `DailyMarketEvidenceService.get_chart()` 加载完整图表。

同一份本地样本包含 28 个 instrument：

- 存储中的整份图表 JSON：10,334,582 字节。
- `get_chart()` 读取并叠加策略后的重新序列化结果：10,255,967 字节；一次函数调用约 112 ms、3 次 SQL。
- 其中 SPY 单个 instrument 的序列化结果约 367,261 字节。

这些数字使用的序列化路径并不完全相同，仅证明量级与读取粒度问题。SPY 数字不含新 API 必需的 envelope、共享字段和 overlays，不能承诺新响应精确为 367 KB。网络压缩未测量。

### 接口与存储建议

1. 保留完整图表接口供现有研究、AI 和其他消费者使用。新增明确的按 symbol 投影方法及响应 schema；路由可以是 `/datasets/{id}/instruments/{symbol}/chart`，最终命名由实现补丁统一。
2. 返回该标的 bars、series、相关 overlays/state segments、质量、Dataset 身份及必要 benchmark 引用。不要遗漏相对强弱序列依赖。
3. 子集定义独立 schema/hash，保留父级证据引用；不能把完整图表原 hash 当作裁剪后内容的 hash。
4. 合法 symbol 的规则依据消费入口明确约定。机会视图仍以 Dataset scope symbols 为准，不能仅因 symbol 出现在 benchmark instruments 中就允许扩大机会范围。
5. worker 新增只读的最小 replay evidence 读取方法，仅获取所需 bars、来源、质量和时间信息，避免为回放构建全部指标 series 和策略 overlays。
6. `get_chart()` 当前会读取 strategy bundle 并重建 overlays/hash。拆分读取和缓存时必须保持同一策略集合的一致性，不能悄悄改成只返回旧的存储 overlays。

实现可分成两层交付：

- **传输优化**：服务端裁剪响应、前端按标的加载，先减少网络和浏览器分配。但如果仍完整读取 ORM JSON，只能宣称客户端收益。
- **存储读取优化**：数据库 JSON 路径投影，或新增按 Dataset/symbol 存储的派生表，真正减少 Python 反序列化和 worker 峰值内存。

优先用原型比较两种存储方式，再决定是否新增表。JSON 路径必须参数化并处理 symbol 特殊字符；SQLite 与 PostgreSQL 使用各自受测实现。新增派生表时采用增量 schema 迁移，不重写历史 Dataset/Chart；历史数据通过原图表只读提取兼容，批量 backfill 另设显式工具、断点和事务边界，不在 GET 中触发。

### 前端修改

- 涉及 `frontend/src/api/client.ts`、`types/dailyEvidence.ts`、`views/InstrumentDecisionView.vue` 和 `components/decision/DecisionChartWorkspace.vue`。
- 当前 `frozenEvidence` 代表整份多标的图表。改成按标的接口后，缓存必须带 Dataset、symbol 和投影/策略身份，不能把 A 标的子集当作 B 标的完整缓存。
- 保留 AbortController、请求代次检查、切换 Dataset 后释放引用、卸载取消。
- 图表加载与 opportunity-view 请求继续可以独立完成，不因裁剪重新引入串行等待。
- 如增加浏览器缓存，限制条数或字节数，避免长期浏览累积所有标的图表。

### 验证与验收

- 裁剪后的 bars、series、overlays 和质量与完整图表对应部分一致，symbol 不串线。
- 覆盖 A → B 快速切换、返回 A、切换 Dataset、晚到响应、404、无 bars、缺少派生记录时的兼容分支。
- 区分并记录响应字节、数据库读取字节/对象数、反序列化耗时、worker RSS；仅测响应变小不足以宣布内存问题解决。
- 兼容旧 Dataset，且 GET 不写库、不采集、不产生新策略决策。
- 测试位置：`test_daily_evidence.py`、`test_opportunity_view_api.py`、`InstrumentDecisionView.spec.ts`、`DecisionChartWorkspace.spec.ts`、`OpportunityViewPanel.spec.ts`。

## 7. PERF-05：canonical daily bars 批量入库

### 已确认的问题

`DailyEvidenceRepository.upsert_bars()` 每条输入先查询完整唯一身份是否存在，再添加 ORM 对象。当前 Session `autoflush=False`。

原审查的 520 根合成日 K 实验使用了默认 `Session(autoflush=True)`，首次入库统计到 520 次 SELECT、520 次 INSERT，约 86.1 ms；相同输入重复导入仍有 520 次 SELECT，约 44.6 ms。这组 INSERT 次数不能代表生产 `autoflush=False` 的行为。实施时重新按生产配置测量：旧实现 520 次 SELECT、1 次批量 INSERT，约 65 ms；优化后 12 次 SELECT、6 次批量 INSERT，约 21 ms。主要收益来自批量查重及减少 ORM 写入开销，不能宣称旧生产实现每根 bar 单独 INSERT。大样本和重复导入对照见实施结果文档。

### 实现步骤

1. 一次性规范化并校验本次输入，使用原算法计算 source revision，不能改变 hash 的字段、格式和语义。
2. 按完整唯一键在输入内去重：`symbol, exchange, bar_date, adjustment, source, source_revision`。同一批输入含重复 bar 时仍应返回符合现有调用方预期的结果顺序和数量。
3. 分块批量读取已有键及必要 ID；块大小考虑 SQLite 参数数量限制和每个键的参数数目。不要构建无限大的 `IN`/`OR`。
4. 批量插入缺失项。可优先采用 SQLAlchemy Core 多行/执行批次，并通过现有唯一约束处理并发插入相同 key 的竞争。
5. 若使用 SQLite/PostgreSQL `ON CONFLICT DO NOTHING`，指定准确冲突目标；不要使用会隐藏其他数据错误的泛化忽略方式。
6. 按输入映射结果，必要时批量读回 ORM 对象，避免把逐条 SELECT 移到返回值构建阶段。
7. 方法继续只 flush，由原业务事务 commit。不要在工具函数内每批 commit，也不要为了重试冲突回滚不相关的上层工作。

### 不可改变的行为

- 原有 bar revision 保留；修订必须追加新记录，不覆盖旧价格。
- 完全相同 bar 重复导入时，原 ID、`collected_at` 和来源时间保持不变。
- 请求早期 cutoff 时仍只能看到当时可见的版本。
- `_reconcile_history_capacity_states()` 在 canonical persistence 的同一事务内维护容量状态。
- 非法 OHLC、非有限数、日期错误继续按原契约拒绝。

### 验证与验收

- 覆盖全新、全部重复、混合新增、同批重复、多 symbol、多 source、QFQ/其他 adjustment、多 revision 和失败回滚。
- 明确核对返回列表语义；在 `autoflush=False` 条件下验证同批重复不会造成唯一约束异常。
- 查询及写入调用次数按批次增长，不再每根 bar 一次查询；记录批次大小和实际语句/执行次数，避免只数 Python API 调用。
- 比较 520-bar 单标的和约 100-symbol × 520-bar 的隔离数据集；分别测首次导入和重复导入。
- 测试位置：`test_daily_evidence.py`、`test_history_quota.py`、`test_sidecar_captures.py`，如涉及跨方言实现补充 PostgreSQL 集成验证。

## 8. PERF-06：Run 列表轻量化

### 已确认的问题

`GET /observation/runs` 使用完整 `ObservationRunResponse`，包含 report、options、alignment 等详情。Discovery 与 CrossSection 会拉取 30 条，再选中一条 Run。本地 5 条 Run 的列表序列化结果为 596,662 字节（使用 `json.dumps(..., default=str)`，不是抓取的 HTTP body）。

特别注意：`ObservationRunView.vue` 当前直接从列表中的 selected Run 读取 report，不能只从后端删字段。

### 推荐兼容方案

1. 保留现有列表响应，新增 `GET /observation/run-summaries` 及独立 `ObservationRunSummaryResponse`。这样避免在同一参数化路由上让客户端猜测返回 schema。
2. 建议摘要字段：run ID、status、trigger mode、trading date、created/completed time、必要的组数和错误摘要。不要直接继承完整 detail schema。
3. Repository 显式 select 所需列。若只在 Python 序列化阶段裁剪，仍会加载整份 `payload_json`，仅解决传输问题。
4. `group_count` 可从 `group_ids` 计算；successful/failed count 当前在 JSON 中，若 UI 确需则做数据库 JSON 标量投影。不要为了这两个数把完整 report/options 加载到 Python；暂不必要的字段可不进入初版摘要。
5. 初版保持当前列表排序与 limit 行为。可选的 status 过滤必须明确“过滤后再 limit”。新增“最新可用”查询若改变当前前端选择策略，作为单独行为变更验证。
6. 静态新路由应在动态参数路由之前注册，避免路径被 Run ID 路由吞掉。

### 前端迁移

- `api/client.ts` 新增 `listObservationRunSummaries()`，`types/api.ts` 新增 Summary 类型。
- Discovery 与 CrossSection 使用摘要选择 Run，然后调用各自 projection 接口。
- ObservationRunView 把“摘要列表”与“选中 Run 的详情”分开；初次选中和切换时调用 `getObservationRun()`。
- 为详情切换增加取消或请求代次检查；加载中不展示上一 Run 的 report 冒充当前结果。
- `createObservationRun()` 保持完整响应；刷新摘要后选中刚创建的 Run，并正确获取详情。
- 旧列表接口暂不删除，待所有消费者明确迁移后再独立清理。

### 验证与验收

- 摘要查询不把整份 `payload_json` 反序列化到 ORM；摘要响应不含 report/options/alignment。
- 列表加载量不随某个 Run 的期权链或报告长度增长。
- 默认选择、空列表、running/failed/mixed、缺失 completed_at、用户手选历史 Run 和新建 Run 后定位行为正确。
- 原 detail API 和历史消费者兼容；前端快速切换不会出现详情错配。
- 测试位置：`test_observation.py`、`DiscoveryView.spec.ts`、`CrossSectionView.spec.ts`、`ObservationRunView.spec.ts`、`api.spec.ts`。

## 9. 测量与测试操作约定

### 基准记录格式

每个实现补丁记录：commit/工作区状态、Python/依赖版本、机器、数据库方言、fixture 大小、是否预热、测量边界、重复次数、结果中位数及范围。生产容量评估另测 P95，不从两三次本地微基准推断。

- SQL：用 SQLAlchemy `before_cursor_execute` 计数，在 fixture 创建之后清零；新建 Session，防止 identity map 隐藏读取。
- ORM：需要验证单结果读取时，额外监听 `load` 事件。只检查返回列表长度无法证明没有加载其他记录。
- CPU：`time.perf_counter()` 测未剖析耗时；cProfile 用于定位函数，不将其耗时与普通执行直接比较。
- JSON：报告 UTF-8 字节数及序列化选项；区分数据库原文、API 序列化响应和压缩后的网络流量。
- 内存：分别记录 API 父进程与 worker 的峰值/稳定 RSS；多次访问后检查是否恢复稳定。平台资源接口的 RSS 单位要确认。
- CI 优先断言语义、查询数量级、只加载所需对象和有界资源行为；避免固定毫秒阈值导致机器相关的不稳定测试。

### 复现 Discovery 的只读测量示例

从仓库根目录运行。数据库文件不存在或 schema 不兼容时停止，不自动创建或迁移数据库。

```bash
PYTHONPATH=backend backend/.venv/bin/python - <<'PY'
from time import perf_counter
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.decision_harness.discovery import DiscoveryProjectionService

engine = create_engine('sqlite:///file:backend/urus.db?mode=ro&uri=true')
count = [0]

@event.listens_for(engine, 'before_cursor_execute')
def record(*args):
    count[0] += 1

with Session(engine) as session:
    start = perf_counter()
    result = DiscoveryProjectionService(session).build(
        '50f77d4e-25ad-4124-934d-22d93bce1064'
    )
    print({'queries': count[0], 'seconds': perf_counter() - start,
           'content_sha256': result['content_sha256']})
engine.dispose()
PY
```

样本 ID 仅是本地复现定位，不应进入产品代码或要求 CI 提供该数据库。长期基准应使用确定性合成 fixture；PERF-01、03、05 的本次临时实验脚本没有保存为仓库测试，需要实施者建立可重复的基准入口。

### 分批验证命令

在 `backend/` 中按修改范围选取：

```bash
uv run pytest tests/test_observation.py tests/test_daily_evidence.py tests/test_strategies.py tests/test_daily_comparison.py
uv run pytest tests/test_daily_opportunity_replay.py tests/test_opportunity_view.py tests/test_opportunity_view_api.py tests/test_buy_opportunities.py
uv run pytest tests/test_history_quota.py tests/test_sidecar_captures.py
```

在 `frontend/` 中，涉及对应页面时运行：

```bash
npm test -- tests/DiscoveryView.spec.ts tests/CrossSectionView.spec.ts tests/ObservationRunView.spec.ts tests/api.spec.ts
npm test -- tests/InstrumentDecisionView.spec.ts tests/DecisionChartWorkspace.spec.ts tests/OpportunityViewPanel.spec.ts
npm run build
```

涉及 schema 时补充 migration 测试。合并全部批次后运行一次 backend/frontend 完整测试与 production build；没有新变化或疑点时不反复重跑。

## 10. 实施分工、交付与当前工作区注意事项

建议按以下可独立审查的补丁交付：

1. **PR-A：PERF-01**。仅单结果查询限量及必要回归验证。
2. **PR-B：PERF-02 第一阶段**。显式复用上下文、查询计数基准；批量加载可另一个提交/PR。
3. **PR-C：PERF-06**。摘要 API 与三个消费页面一起迁移。
4. **PR-D：PERF-05**。批量入库、版本可见性与事务验证。
5. **PR-E：PERF-04**。先约定子集契约，再完成 API、前端、worker 及旧数据兼容；区分传输优化与存储读取优化的交付范围。
6. **PR-F：PERF-03 第一阶段**。只优化单次回放，并提供语义对照与 CPU/RSS 基准；跨请求缓存另立补丁，经过评估再决定是否实施。

如果由多个 agent 并行实施，先分配文件责任并处理重叠：PR-A/B/C 都可能改 `repositories/observation.py` 与 `test_observation.py`；PR-A/D/E 都可能改 `repositories/daily_evidence.py`；PR-C/E 都改前端 API client；PR-E/F 都改 opportunity service/worker。不要让各 agent 独立覆盖这些文件。

审查开始时已有用户/其他任务的未提交修改，包含：

- `frontend/src/styles/cross-section.css`
- `frontend/src/views/CrossSectionView.vue`
- `frontend/tests/CrossSectionView.spec.ts`
- `scripts/deploy_apple_moomoo.sh`
- `urus-moomoo/README.md`
- `urus-moomoo/src/urus_moomoo/cli.py`
- `urus-moomoo/src/urus_moomoo/publisher.py`
- `urus-moomoo/tests/test_deploy_config.py`
- 未跟踪的 `.codex/config.toml`、sidecar `preflight.py` 和 `tests/test_preflight.py`。

该列表只是当时快照，实施前重新检查 Git 状态。保留他人修改，尤其是 PERF-06 会触及的 CrossSection 文件。本文不要求部署或清理任何现有数据。

每个补丁最终交付应包含：修改的行为与文件、保留的兼容性、测试结果、同样输入下的前后测量、未覆盖的部署/方言场景。不要以“加了缓存/批量查询”作为完成标准；用输出一致性和实际资源变化说明收益。
