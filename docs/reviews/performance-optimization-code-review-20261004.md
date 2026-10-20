# Urus 性能优化评估与代码审查（2026-10-04）

## 结论

基础优化的性能收益成立；本轮复测 Discovery 为 31 次 SQL、预热中位数 20.77 ms，SPY 366 bars 机会视图预热中位数 368.11 ms，与前次交付结果相符。该历史机会视图仍与实施前保存的完整 JSON 一致。

代码审查发现的 **2 个 P2 边界回归均已修复并通过回归验证**。修复后后端全量 **315 passed**，SPY 常规样本仍与优化前完整输出一致。没有发现可确认的 P0/P1 问题。下文保留原发现及复现说明，修复验收记录见文末。

范围为本轮性能优化的后端、前端消费和测试。工作区此前已有的 CrossSection 可视化布局、moomoo 部署及 sidecar 改动不作为本次性能补丁的新增行为审查。

## R1 / P2：当前标的没有新策略决策时，会保留旧策略图形

状态：**已修复**。已有 synthesis 的空决策也执行图形重建，清除旧策略；完全没有 bundle 的历史图表保留原兼容路径。

位置：[market_evidence.py:332](/Users/waynewong/code/Urus/backend/app/decision_harness/market_evidence.py:332)，`get_instrument_evidence()` 的 `if decisions` 分支。

### 触发条件与影响

冻结图表包含旧策略集的标的 A overlays/state segments；最新 synthesis/strategy bundle 只包含标的 B，当前请求标的 A。这样的部分 bundle 可以由现有 Repository 表达和保存；标准 `freeze()` 通常会生成所有 primary symbols 的决策，所以这是稀疏/历史 bundle 的边界，不是每次普通请求都会发生。

`bundle(dataset_id, symbol=A)` 返回空 decisions，但非空的最新 synthesis。`if decisions` 跳过 `with_overlays()`，因而跳过它清除旧 `strategy_decision_id` 图形的逻辑。随后响应又将 `strategy_set_sha256` 标为最新策略集。

结果是同一响应声明新策略集、返回空决策，却展示旧策略区间/价位。原完整 `get_chart()` 在 B 有决策时会重建整份图表，清掉 A 的旧图形；因此这也是新接口与原接口裁剪结果的不一致。

### 实际复现

在独立内存 SQLite 中：

1. 调用 `freeze(scope_type="group", scope_id="review", symbols=["AAA", "BBB"], cutoff_time=...)` 创建旧图表和策略。
2. 从旧 decisions 中取 BBB，给它们新的 decision ID 和 strategy set。
3. 调用 `deterministic_synthesis(dataset, bbb_decisions, "new-set")`，再用 `save_bundle()` 保存为更晚的 bundle。
4. 比较 `get_chart(dataset_id)` 与 `get_instrument_evidence(dataset_id, "AAA")`。

复现结果：

```text
完整图表中 AAA state_segments：0
新单标的图表中 AAA state_segments：5（全部引用旧 decision ID）
新单标的 strategy_decisions：0
新单标的 chart.strategy_set_sha256：new-set
```

### 修改建议与验收

- 区分“完全没有可用 bundle”和“已选定最新 bundle，但该标的没有决策”。后者仍须执行 `with_overlays(chart, [])` 清除旧策略图形。
- 原图表中的非策略 overlays/state segments 可保留；不得以旧策略补齐缺失的新决策。
- 为“旧 A+B、新 B-only”补回归，断言 A 的旧 strategy overlays/state segments 消失，响应 strategy set 与 synthesis 一致。
- 同时覆盖“没有任何 synthesis”的历史兼容分支，明确其降级契约，避免为修复此问题无意改变所有历史图表的兼容行为。

## R2 / P2：快速 reader 未保留原 manifest 规范化语义

状态：**已修复**。快速返回要求 manifest 数量、日期顺序和 session_id 与 bars 一一对应；其他输入使用原 reader 重建。

位置：[daily_replay.py:416](/Users/waynewong/code/Urus/backend/app/buy_opportunities/daily_replay.py:416)，`_ReplayEvidenceReader.load()` 的快速返回条件。

### 触发条件与影响

`FrozenEvidence` 要求 bars 排序且日期唯一，但对 `source_manifest` 通过按日期映射校验，没有要求引用列表按日期排序或禁止重复。研究入口也允许把 JSON 直接构造为 `FrozenEvidence`。因此“manifest 非空且模型校验通过”不等于“与原 reader 的规范化输出完全相同”。

原 `InMemoryDailyEvidenceReader` 调用 `freeze_daily_evidence()`，会按照 bars 重建有序、一根一条的 manifest，且 session_id 来自 bar。新快速路径直接返回原引用列表。相同有效输入在默认 `include_scan_manifests=True` 下产生不同的 scan ID/content hash，破坏回放输出及身份等价性。

普通 `freeze_daily_evidence()` 生成的有序 manifest 不触发本问题；当前 SPY 页面样本也未触发。影响集中于合法但未规范化的冻结证据/研究输入，不能据普通样本通过就推广到全部有效输入。

### 实际复现

以下片段在仓库根目录用 `PYTHONPATH=backend backend/.venv/bin/python` 执行，不读写业务数据库：

```python
import runpy
from unittest.mock import patch
from app.buy_opportunities.contracts import FrozenEvidence
from app.buy_opportunities.daily_replay import replay_daily_opportunities
from app.buy_opportunities.evidence import InMemoryDailyEvidenceReader

fixture = runpy.run_path("backend/tests/test_daily_opportunity_replay.py")
rows = fixture["_history"]()
raw = fixture["_evidence"](rows).model_dump(mode="python")
raw["source_manifest"] = list(reversed(raw["source_manifest"]))
evidence = FrozenEvidence.model_validate(raw)  # 校验通过，没有绕过模型
calendar = fixture["_calendar"](rows)
optimized = replay_daily_opportunities(evidence, calendar)

class NormalReader:
    def __init__(self, value):
        self.reader = InMemoryDailyEvidenceReader({value.symbol: value})
    def load(self, *args, **kwargs):
        return self.reader.load(*args, **kwargs)

with patch("app.buy_opportunities.daily_replay._ReplayEvidenceReader", NormalReader):
    reference = replay_daily_opportunities(evidence, calendar)

print(optimized == reference)  # False
print(sum(a.scan_id != b.scan_id
          for a, b in zip(optimized.scans, reference.scans)))  # 68 / 70
```

两边使用同一个前缀缓存实现，只替换 reader，隔离了问题来源。此样本首个 setup ID 相同，不能把本发现表述为必然改变交易触发价或 setup ID；已确认的是 scan 身份和完整回放输出改变。

### 修改建议与验收

- 在回放入口一次性将 manifest 规范化为原 `freeze_daily_evidence()` 的输出，或为快速路径增加充分的规范化条件，不满足时回退原 reader。
- 规范化必须包含按 bar 日期一一对应、顺序、重复项以及 session_id；不能只比较引用数量。
- 用经过正常 `FrozenEvidence.model_validate()` 的逆序、重复引用和 session_id 差异输入，比较优化前后的完整 scan/setup/trigger/step 输出。
- 保留正常 manifest 的快速路径，修复后复测 CPU，避免重新在每个前缀重复构建完整证据。

## 性能结果评估

| 项目 | 判断 | 证据及边界 |
| --- | --- | --- |
| 查询限量 | 方向正确 | SQL 层 `LIMIT 1`，回归验证单个 ORM 对象加载；没有改变筛选与排序条件。 |
| Discovery | 第一阶段达标 | 本轮六次均为 31 次 SQL；后五次中位数 20.77 ms，前次记录 24.50 ms。仍有按组查询，未完成第二阶段批量读取。 |
| 回放 CPU | 常规输入收益明确 | 审查时三次预热中位数 368.11 ms；修复后五次中位数 383.44 ms，历史 SPY 输出与旧基线相同，R2 特殊输入已补回归。 |
| 单标的图表 | 传输和 Python 加载量下降 | SQL JSON 投影避免完整 Chart ORM；此前完整证据 11.41 MB→1.11 MB。R1 的空决策边界已修复。 |
| 入库 | 批量查重收益明确 | 完整唯一键、准确 conflict target、调用方事务、输入顺序均保留；52,000 根样本 5.36→1.52 s。计数是 cursor execution，不能宣称旧实现逐行 INSERT。 |
| Run 摘要 | 达标 | 独立 schema、显式列/JSON 标量、原接口保留；前端按需详情与取消/晚到响应保护完整。 |
| 内存 | 有收益，但尚未完成容量验收 | 此前 248.5→212.1 MiB 是相同优化后算法下，完整/最小图表读取的隔离进程对照；不是整个旧/新在线服务的内存比较。 |

SQL 次数、序列化字节和 ORM 对象加载量是较强的结构性证据。耗时样本较少，尚未包含 HTTP、浏览器渲染、真实并发 P95；现阶段适合认定“局部瓶颈得到改善”，不适合推断整页响应加速倍数或线上容量。

## 初次审查验证记录

- 重新运行性能查询、证据、日线回放和 worker 四个测试文件：**23 passed**。这说明现有回归仍绿，也说明其未覆盖 R1/R2。
- 新增检查使用临时内存库和纯函数脚本复现，没有向仓库加入失败测试，没有修改业务代码。
- 只读复测 Discovery 和 SPY 回放；没有迁移或写入历史库。
- 上一轮全量 **302 backend / 88 frontend** 与生产构建通过，本轮没有重复整套无变化测试。该通过记录不能替代以上新增边界的验收。

建议顺序：先修复 R1/R2 并补等价性回归，再进行实际页面冷启动与并发测量；Discovery 进一步批量读取可随后做。跨请求完成结果缓存暂不优先，现有单次回放约 0.37 秒，新增缓存会增加身份和失效复杂度。PostgreSQL 集成仍是明确的验证缺口，不作为已通过项。

## 修复与验收记录（2026-10-04）

R1 在 `get_instrument_evidence()` 将重建条件改为 `decisions or synthesis`。当最新 bundle 存在但选中标的没有决策（或整个 bundle 没有决策），仍通过 `with_overlays(chart, [])` 去除旧策略图形。共享/非策略图形保留；没有 synthesis/decisions 的历史图表继续兼容，不冒充新策略集。

R2 在 `_ReplayEvidenceReader` 初始化时检查规范化条件：引用数量与 bars 相同、按 bar 日期一一对应、session_id 相同。原 FrozenEvidence 校验已保证引用的来源、revision、available_at 和内容 hash 匹配；新增条件覆盖此前遗漏的顺序、重复和 session 身份。非规范引用回退到原 `InMemoryDailyEvidenceReader`，按 bars 重建。空 bars 还保留原质量严重度合并，避免快速返回将空窗口的 ok/partial 状态保留下来。

补充 **13 个回归用例**：

- 3 个 API 用例：最新部分 bundle、最新空 bundle、完全没有 bundle；验证旧策略 overlays/state segments 清理、共享图形保留、策略集/hash 一致及历史兼容。
- 6 个 reader/完整回放对照：逆序、重复引用、session_id 差异 × manifests 开/关；所有输入通过正常 `FrozenEvidence.model_validate()`，比较完整 scan/setup/trigger/steps 序列化输出。
- 4 个空证据质量用例：ok/partial/unavailable/failed，与原 reader 比较。
- 原正常 manifest 用例增加对象身份断言，确认规范输入仍使用快速路径。

修复前新增边界测试复现 **8 failed / 1 passed**；修复后先完成对应测试，再运行 `.venv/bin/python -m pytest`，最终 **315 passed，37.80 s**。本轮前端未变化，沿用此前 88 个测试及构建通过记录。`git diff --check` 通过。

同一只读 Dataset/SPY/固定 viewed_at 复测，预热 5 次耗时为 `[0.392435, 0.393474, 0.383440, 0.379667, 0.382506]` 秒，中位数 **0.383440 s**，仍约为最初 1.872 s 基线的五分之一。测量期间也运行后端测试，不将与前次 0.368 s 的差异解释为统计显著的回退。完整 JSON 与优化前保存结果相同。

复核新增分支与测试后，R1/R2 可关闭。修改仍保留在工作区；线上并发/浏览器/长期 RSS 和 PostgreSQL 实库验证继续作为后续事项。

详细实施背景见 [性能优化实施结果](/Users/waynewong/code/Urus/docs/reviews/performance-optimization-delivery-20261004.md)。
