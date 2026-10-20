# Urus 日线 MACD 三层数据测试报告

日期：2026-10-15\
范围：P0/P1 日线 MACD 柱值、柱值一阶差分、柱值二阶差分\
测试文件：[backend/tests/test_macd_momentum_acceptance.py](../../backend/tests/test_macd_momentum_acceptance.py)

投影测试文件：[backend/tests/test_macd_projection_acceptance.py](../../backend/tests/test_macd_projection_acceptance.py)

## 本轮目标

本轮只验证公共技术指标入口，不连接 OpenD、moomoo、SSH、生产容器或部署环境。测试通过 `calculate_technical_indicators()` 和 `calculate_technical_series()` 观察行为，使用独立的 SMA 播种 EMA 参考计算，避免复制业务实现的私有函数。

约定的最小公开契约为：

- `macd_12_26_9.histogram_delta`：由未舍入的 H 序列计算 `H[t] - H[t-1]`，最后一次显示舍入。
- `macd_12_26_9.histogram_delta2`：由未舍入的 ΔH 序列计算 `ΔH[t] - ΔH[t-1]`，最后一次显示舍入。
- `macd_histogram_delta` 与 `macd_histogram_delta2`：与输入日线等长的序列，预热不足的点为 `null`，真实计算出的零保留为 `0.0`。
- 现有 `histogram`、`previous_histogram`、`crossover`、`zero_axis`、`momentum` 等字段保留。

## 覆盖内容

测试覆盖以下行为：

1. 用独立 EMA 参考值验证未舍入差分，专门选择了“先舍入 H 再差分”会产生不同结果的数据。
2. 验证 33、34、35、36 根样本边界：H 首次出现在第 34 根，ΔH 首次出现在第 35 根，Δ²H 首次出现在第 36 根。
3. 验证 H 与 ΔH 的四种正负组合。
4. 验证 H 为负、ΔH 为负而 Δ²H 为正时，含义仍是“恶化减速”，不能误报为已经改善。
5. 验证 MACD 真实零值与预热不足的缺失值保持区分。
6. 验证旧字段仍存在，并验证新增序列的长度、索引对齐和预热 `null` 行为。序列首有效索引分别是 H=33、ΔH=34、Δ²H=35（零基索引）。

投影验收另外覆盖：

- 组快照把成员级 ΔH/Δ²H 传递到 `symbols[]`，短历史成员保持 `null`；
- 指标目录暴露两个新 lens，能通过 HTTP 选择；
- 旧冻结快照缺少新字段时，旧 `macd_histogram` lens 仍可读，新 lens 返回 200、`value: null`、`status: missing`，而不是返回 404 或把缺失当成零；
- service registry 与 HTTP catalog 保持一致。

## 当前红灯证据

执行命令：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_macd_momentum_acceptance.py
```

结果：`13 failed`。

失败原因集中在预期的新契约尚未实现：

- `macd_12_26_9` 尚未提供 `histogram_delta` 和 `histogram_delta2`，边界、精度、四象限、恶化减速及零值测试因此失败。
- `calculate_technical_series()` 尚未提供 `macd_histogram_delta` 和 `macd_histogram_delta2` 两个序列，因此序列对齐和零／缺失测试失败。

旧字段相关的断言在进入新增序列断言前未发现兼容性错误；开发完成后需重新运行整组测试确认。

环境备注：直接调用系统 `pytest` 时缺少 `fastapi`；改用仓库已有的 `backend/.venv/bin/pytest` 后，测试按预期进入业务红灯。未运行全套系统测试。

投影测试执行命令：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_macd_projection_acceptance.py
```

结果：`4 failed`。

失败原因分别是：组成员尚未传播新字段；catalog 尚未注册两个新 lens；旧快照请求新 lens 返回 404；service registry 尚无两个新 ID。上述失败均对应待实现契约。修正后的零值序列测试不再产生自相矛盾的失败。

## 开发后的绿色复测

后端实现完成后重新执行两组验收：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_macd_momentum_acceptance.py backend/tests/test_macd_projection_acceptance.py
```

结果：`19 passed`。这覆盖了未舍入 H/ΔH/Δ²H、34/35/36 根边界、四象限、恶化减速、零与缺失、旧字段兼容、序列对齐、组成员传播、指标目录、service registry、`technical_v5` 旧冻结快照读取，以及 raw lens 的跨标的不可比行为。

另执行限定范围的日线回归：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_technical.py backend/tests/test_daily_evidence.py
```

结果：全部通过（`test_technical.py` 与 `test_daily_evidence.py` 共 28 项收集用例）。这次回归没有访问 OpenD、moomoo、SSH 或生产容器；唯一提示是测试依赖中的 Starlette/httpx 弃用警告，不影响结果。

legacy fixture 的 `payload_json.feature_version` 已固定为 `technical_v5`，新版组快照 fixture 仍跟随当前 `FEATURE_VERSION`，因此旧读兼容测试不会因实现版本升级而失效。

随后追加了 raw ΔH/Δ²H 的截面语义回归：每个 lens 使用 4 个有效标的和互不相同的原始值，要求 attention 百分位不可用，组摘要只保留覆盖数量而不输出跨价格尺度的 median/min/max。开发禁用 raw lens 的幅度排名后已复测：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_macd_projection_acceptance.py
```

最终结果：`6 passed`。两个 raw lens 均返回 `comparable_across_symbols: false`，各 attention 百分位为 `null`，组 distribution 保留 `count` 而把 median、四分位数、min/max 置为 `null`。

## 开发完成后的验收命令

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_macd_momentum_acceptance.py
PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_macd_projection_acceptance.py
```

绿灯后，再由协调 agent 决定是否补充日线冻结结果／组截面兼容测试。当前报告不包含生产部署、容器替换或外部行情验证。

## 最终收尾复测

上一节的 `19 passed` 与 `6 passed` 是加入旧缓存契约测试前的阶段结果；以下结果覆盖本轮最终测试文件和最终代码。

### 后端验收与回归

新增验收最终为 `20 passed`：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_macd_momentum_acceptance.py \
  backend/tests/test_macd_projection_acceptance.py
```

其中新增的 `test_freeze_uses_macd_extension_snapshot_without_reusing_legacy_cache` 预置了同一 symbol/date/bar hash 的 `technical_v5` 指标快照，确认冻结流程创建 `INDICATOR_SNAPSHOT_FEATURE_VERSION` 的 MACD 扩展快照，旧 payload 不变，重复冻结复用同一 dataset 和扩展快照，且 dataset payload 含 `macd_momentum_version`。raw ΔH/Δ²H 的 HTTP 投影还验证了 `+0.000123` 与 `-0.000045` 的六位显示精度。

限定范围的既有回归最终为 `75 passed`（未运行全仓测试）：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_technical.py \
  backend/tests/test_daily_evidence.py \
  backend/tests/test_observation.py
```

收集数量为 `test_technical.py: 19`、`test_daily_evidence.py: 9`、`test_observation.py: 47`。唯一提示仍是测试依赖中的 Starlette/httpx 弃用警告。

### 前端构建与隔离浏览器验收

前端构建通过：

```text
npm --prefix frontend run build
```

输出为 Vite 75 modules transformed，退出码 0。

Playwright 使用现有 CLI，在本机回环地址启动脚本自有的临时 Vite 进程和固定 HTTP fixture 服务；fixture 不启用 SQLite、provider、OpenD 或任何远程 API。脚本退出码为 0，以下 10 个验收流程全部通过：

1. indicator catalog 暴露 H/ΔH/Δ²H；
2. 个股最新 H/ΔH/Δ²H 使用最后 bar，并显示 `+0.000123`、`-0.000045` 等非零小数；
3. 个股 MACD 图表可在 H、ΔH、Δ²H 间切换且图形与 pane 标签变化；
4. 组成员 INTC/NVDA/AMD/LITE 传递 MACD 三层数据；
5. 旧数据缺少新字段时显示“未计算此版本”；
6. 最新 bar 的 ΔH/Δ²H 为 null 时显示“样本不足”，不回找上一 bar；
7. raw ΔH 截面显示精度正确；
8. raw Δ²H 截面显示精度正确；
9. 两个 raw lens 仅提供状态／symbol 排序和状态筛选；
10. 两个 raw lens 不显示跨标的幅度图或误导性的数值分布。

可复查的测试脚本和证据：

- 脚本：`/private/tmp/urus-intraday-review-20261015/playwright-smoke.sh`
- 固定服务：`/private/tmp/urus-intraday-review-20261015/isolated_fixture_server.py`
- 快照目录：[output/playwright/urus-intraday-review-20261015](../../output/playwright/urus-intraday-review-20261015)
- 最终浏览器截图：[page-2026-10-15T09-47-40-843Z.png](../../output/playwright/urus-intraday-review-20261015/.playwright-cli/page-2026-10-15T09-47-40-843Z.png)

脚本的退出 trap 已终止本轮由脚本创建的 fixture 和 Vite 进程；未连接、重启、替换或部署任何现有容器，也未访问交易日生产服务。浏览器验收使用固定 fixture，因此不替代真实 OpenD/moomoo 数据源验收。

## MACD 信号卡与历史转折标记：新一轮红灯

用户新增需求要求个股详情页明确标记 H、ΔH、Δ²H 的正负状态，并在历史图表标出相邻有效点的符号转折。本轮先增加后端行为验收，未修改业务代码。

红灯命令：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_macd_signal_markers_acceptance.py
```

当前结果：`8 failed`。失败均集中在尚未实现的新字段：`histogram_signals`、`momentum_context` 和三条 MACD histogram series 的 `markers`。

本轮最小契约：

- `macd_12_26_9.histogram_signals` 包含 `histogram`、`histogram_delta`、`histogram_delta2` 三项；每项包含 `sign`、`transition`、`label`。符号使用六位返回值判断，支持 `positive`、`negative`、`zero`、`unavailable`；转折支持 `negative_to_positive`、`positive_to_negative`、`zero_to_positive`、`zero_to_negative`、`to_zero`、`none`、`unavailable`。
- `macd_12_26_9.momentum_context.label` 对 H<0、ΔH<0、Δ²H>0 明确表达“下行动量仍增强，但恶化减速”，不能标为已转多。
- `macd_histogram`、`macd_histogram_delta`、`macd_histogram_delta2` 的每条 series 增加 `markers`；元素包含 `time`、`type`、`label`、`from_sign`、`to_sign`、`value`。只在相邻且均已就绪的点发生符号变化时生成，不为首个有效点或不可用点生成 marker。

测试文件：[backend/tests/test_macd_signal_markers_acceptance.py](../../backend/tests/test_macd_signal_markers_acceptance.py)。

## MACD 信号卡与历史转折标记：最终收尾

后端信号验收最终为 `8 passed`，与前轮 20 项 MACD 验收合并为 `28 passed`：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_macd_signal_markers_acceptance.py \
  backend/tests/test_macd_momentum_acceptance.py \
  backend/tests/test_macd_projection_acceptance.py
```

收集数量为 `test_macd_signal_markers_acceptance.py: 8`、`test_macd_momentum_acceptance.py: 13`、`test_macd_projection_acceptance.py: 7`。验收包含 H/ΔH/Δ²H 状态卡字段、六位数值口径、负正／正负／零轴转折、`to_zero`、首个有效点和不可用点不生成假 marker，以及“下行动量仍增强，但恶化减速”的组合语义。

缓存契约同时验证了旧 `technical_v5` 和旧 `technical_v5:macd_histogram_momentum_v1` 快照不会被 v2 复用：新冻结创建 `technical_v5:macd_histogram_momentum_v2` 扩展快照，旧 payload 保持不变，重复冻结幂等，dataset payload 保留 `macd_momentum_version`。

相关既有回归最终为 `75 passed`：

```text
PYTHONPATH=backend backend/.venv/bin/pytest -q \
  backend/tests/test_technical.py \
  backend/tests/test_daily_evidence.py \
  backend/tests/test_observation.py
```

前端构建最终通过：

```text
npm --prefix frontend run build
```

Vite 报告 75 modules transformed，退出码 0；未运行前端单元测试。

### 最终隔离 Playwright

在固定 loopback fixture 上运行 `/private/tmp/urus-intraday-review-20261015/playwright-smoke.sh`，脚本退出码 0。新增检查和既有检查均通过：

- 个股详情页显示 H、ΔH、Δ²H 三张状态卡，并使用后端 label 和 momentum context；
- H、ΔH、Δ²H 图表切换时显示对应序列的 marker，marker title 包含转折标签、日期、from/to sign 和六位值；
- 当前 fixture 的 H/ΔH 显示“负转正”历史标记，Δ²H 显示“正转负”历史标记；
- 旧 projection 缺少 `histogram_signals`/`markers` 时，状态卡和图表显示“未计算此版本”；
- 末端 ΔH/Δ²H 为 null 时只显示“信号样本不足”和“最新 bar 样本不足”，不复用之前的 marker 或 `+9.999`/`-8.888` 值；
- 原有组成员、raw lens 状态筛选、不可比幅度展示和六位小数检查继续通过。

本轮第一次 Playwright 运行确实捕获了 null-tail 卡片复用旧 marker 的问题；修复后第二次完整脚本通过。最终截图：[page-2026-10-15T10-00-21-629Z.png](../../output/playwright/urus-intraday-review-20261015/.playwright-cli/page-2026-10-15T10-00-21-629Z.png)，完整快照目录：[output/playwright/urus-intraday-review-20261015](../../output/playwright/urus-intraday-review-20261015)。

fixture 与 Vite 均由脚本在本机回环地址启动，并由退出 trap 清理；没有访问生产、OpenD、moomoo、SSH，也没有部署或替换容器。fixture 用于 UI 契约检查，MACD 数学和缓存行为以本地后端验收为准。
