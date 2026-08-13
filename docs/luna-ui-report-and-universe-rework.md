# Luna 修改指令：标的配置与研究报告重构

> 状态：运行设置部分已实现；Universe 与报告重构待实现  
> 负责人：Luna  
> 目标分支：CTA research branch  
> 优先级：P0（报告可用性）+ P1（标的配置）  
> 本文只定义产品与实现要求，不表示功能已经完成。

## 1. 本轮要解决的问题

当前版本已经能采集数据、生成技术报告与 AI 报告，但产品界面仍有三个明显问题：

1. 没有统一的 ETF/股票配置页面。标的列表分散在 `.env` 与后端 Settings 中，用户无法知道一个标的会参与哪些采集和分析。
2. AI 报告把所有内容连续摊开。市场判断、关注标的、风险、缺失字段、证据和期权过滤同时出现，没有清晰的阅读层级。
3. 技术报告不是“内容不够”，而是表达方式不合格。关键变化、异常、趋势和期权结构没有被图表突出，原始表格和辅助字段占据主视图。

核心原则：**UI 是决策工作台，不是工作流笔记、PPT，也不是 JSON 浏览器。**

用户打开报告后，应当先在 10 秒内回答：

- 当前市场是什么状态；
- 哪些标的最值得关注，为什么；
- CTA 边际压力和 IV/HV 是否有异常；
- 哪些条件会改变当前判断；
- 需要时到哪里查看完整证据和原始数据。

## 2. 本轮范围与优先顺序

### P0：先完成报告页重构

1. 重做报告公共页头与一级 Tab。
2. 重做 AI 现状分析/正式决策的信息架构。
3. 重做技术报告总览、个股技术和期权结构展示。
4. 复用开发工具已有 Gamma 曲线和行权价结构图。
5. 把原始数据、缺失字段和完整证据放入抽屉或折叠区。

### P1：再完成 ETF/股票配置页

1. 建立统一标的配置模型和 API。
2. 新增 `/settings/universe` 页面。
3. 采集、CTA、期权、事件和 AI 使用同一份配置快照。
4. 每份 Dataset/报告记录当时使用的 universe 版本，历史报告不被新配置改变。

### 本轮不做

- 不改变 CTA 算法、IV/HV 公式或 AI 决策逻辑。
- 不新增自动下单。
- 不在普通报告页面提供任意 JSON 编辑。
- 不把开发工具搬进研究报告。
- 不为了视觉效果制造没有数据支持的图表。

---

## 3. 运行设置页（先于 Universe 设置实现）

本轮先新增 `/settings`，只处理日程与模型入口；`/settings/universe` 仍按后续 P1 设计，不在本次实现。

### 3.1 日程控制

设置页必须把两次正式 AI 日程拆成独立的开关：

- **盘前正式决策**：可停用；可选择“执行采集但不启动 AI 决策”。
- **收盘复盘**：可停用；可选择“执行采集但不启动 AI 决策”。
- **尾盘数据采集**：可停用，但始终固定为只采集数据，不启动 AI，不生成正式决策。

调度器每次处理到期槽位时读取运行时设置。设置修改不要求重启；已经冻结的 Dataset、报告和预测评分不被回写改变。尾盘的 `skip_ai_decision=true` 是后端安全边界，不允许 UI 绕过。

### 3.2 模型入口

设置页显示并允许修改：

- Urus AI 决策模型（当前通过 OpenRouter 的 `provider/model` 标识）；
- Anomalo 检索 Agent（预设名称）。

Anomalo 当前接口调用的是 `/api/agents/{agent}`，请求参数不会单独选择底层模型。因此 UI 必须明确写出：实际检索模型由 Anomalo 预设 Agent 在 Anomalo 端配置，本页选择的是 Agent，不应伪造一个无法从 Urus 验证的模型名。API Key 等凭据继续由环境变量管理，不在普通设置页保存。

### 3.3 持久化与并发

运行时设置使用单例 `runtime_settings` 表保存非敏感配置，并带递增 `revision`。保存接口携带当前 revision；发生并发修改时返回 `409 settings_revision_conflict`，前端保留编辑内容并提示重新读取。环境变量是没有运行时覆盖时的默认值。

API：

```text
GET /api/settings
PUT /api/settings
```

响应包含 `source`、`revision`、更新时间、三类日程开关和模型入口说明。保存后新的调度周期读取最新配置。
同时返回 AI 运行时是否启用、OpenRouter 凭据是否已配置，避免界面把“启动 AI 决策”误读成一定会成功调用。

## 4. ETF/股票设定页面

### 4.1 路由与导航

新增：

```text
/settings
/settings/universe
```

主导航增加“设置”，不要把它放入“开发工具”。普通用户配置研究范围不属于开发调试。

建议导航：

```text
首页 | 研究中心 | 手动分析 | 历史报告 | 设置 | 开发工具
```

### 4.2 不要直接把多个环境变量做成输入框

当前后端存在多个相互重叠的列表：

- `instrument_validation_symbols`
- `moomoo_market_symbols`
- `options_target_symbols`
- `options_watchlist_symbols`
- `options_watchlist_excluded_symbols`
- `cta_proxy_symbols`
- `event_instrument_symbols`
- `enabled_symbols`

这些是实现细节，不应原样暴露给用户。前端需要编辑统一的 `InstrumentConfig`，后端再根据角色生成各步骤需要的集合。

建议领域模型：

```json
{
  "symbol": "QQQ",
  "display_name": "Nasdaq 100 ETF",
  "asset_type": "etf",
  "theme": "ETF",
  "enabled": true,
  "roles": {
    "market_benchmark": true,
    "equity_watchlist": true,
    "cta_proxy": true,
    "options_collection": true,
    "event_tracking": false,
    "ai_candidate": true
  },
  "benchmarks": {
    "relative_strength": "SPY",
    "cta_proxy_for": "NQ equity-index futures"
  },
  "collection": {
    "quote": true,
    "daily_history": true,
    "options": true
  },
  "notes": "Core market benchmark"
}
```

必须区分：

- ETF 与股票；
- 市场基准与普通观察标的；
- CTA 代理与真实股票；
- 是否采集期权；
- 是否进入 AI 候选池；
- 是否参与事件跟踪。

一个标的可以有多个角色，但角色含义必须可见。

### 4.3 页面布局

```text
┌──────────────────────────────────────────────────────────────┐
│ 研究标的设置                           [保存更改] [恢复默认] │
│ 当前版本 v12 · 27 个启用 · 最近保存时间                     │
├──────────────────────────────────────────────────────────────┤
│ [全部] [ETF] [股票] [CTA代理] [期权] [AI候选]   搜索 Symbol │
├──────────────────────────────────────────────────────────────┤
│ Symbol  类型  题材     角色                    状态    操作  │
│ QQQ     ETF   ETF      市场/CTA/期权/AI        已启用   编辑  │
│ NVDA    股票  半导体   个股/期权/事件/AI       已启用   编辑  │
│ ...                                                          │
├──────────────────────────────────────────────────────────────┤
│ [+ 添加标的]                                                 │
└──────────────────────────────────────────────────────────────┘
```

编辑使用右侧抽屉，不要跳转到一张长表单页。抽屉分组：

1. 基本信息；
2. 研究角色；
3. 数据采集；
4. 基准与 CTA 映射；
5. 影响预览。

“影响预览”必须用自然语言说明：

```text
保存后：
• 下一轮 3A 会采集 NVDA 日线和技术指标
• Stage 2 会采集 NVDA 期权
• AI 可将 NVDA 列为关注标的
• 已冻结的历史报告不会改变
```

### 4.4 必要交互

- 支持按 Symbol/名称搜索。
- 支持 ETF/股票和角色过滤。
- 支持批量启用、停用以及批量设置角色。
- 新增 Symbol 时先校验格式和重复项。
- 保存前显示差异摘要：新增、停用、角色变化。
- 保存失败不能丢失编辑状态。
- `options_collection=true` 时提示数据费用和 Moomoo 权限要求。
- CTA proxy 必须填写其代表的期货/风险因子，不允许只有一个模糊开关。
- 删除优先使用“停用”；已被历史 Dataset 引用的标的不可物理删除。

### 4.5 配置版本与数据血缘

建议新增：

```text
instrument_universe_versions
instrument_universe_items
```

每次保存创建不可变版本。Run 启动时读取最新 active 版本，并把以下字段写入冻结 Dataset：

```json
{
  "universe_version_id": "...",
  "universe_content_sha256": "...",
  "requested_symbols": [],
  "roles_by_symbol": {}
}
```

禁止运行过程中读取后来修改的配置。历史报告必须始终按当时的 universe 解释。

### 4.6 API 建议

```text
GET  /api/settings/universe
GET  /api/settings/universe/versions
POST /api/settings/universe/validate
PUT  /api/settings/universe
GET  /api/settings/universe/symbols/:symbol/impact
```

保存接口必须接收 `base_version_id`，避免两个页面覆盖彼此修改。

---

## 5. 报告页面公共框架

### 5.1 页头只保留判断所需信息

当前 provider、model、token、Skill hash、schema、内部 Session 等信息占据主视觉。调整为：

首行：

- 报告类型：正式盘前 / 正式复盘 / 手动现状；
- 数据截止时间；
- 成功/部分成功/失败；
- 数据质量。

第二行操作：

- 查看来源；
- 运行信息；
- 同 Dataset 的其他版本；
- 重跑 AI（有权限时）。

provider、model、token、耗时、hash、schema 放进“运行信息”抽屉，默认不展开。

### 5.2 一级 Tab

```text
技术报告 | AI 判断 | 工作流验证
```

Tab 固定在报告内容顶部，滚动时 sticky。URL 必须保存：

```text
?tab=technical
?tab=decision
?tab=trace
```

切换 Tab 不重新请求已经加载的数据。

### 5.3 页面宽度和间距

目标不是把屏幕塞满。统一规则：

- 阅读内容最大宽度：`1180–1280px`；
- 主区左右内边距：桌面 `28–36px`，移动端 `16px`；
- section 间距：`32px`；
- section 标题到正文：`14–18px`；
- 卡片内边距：`18–22px`；
- 列表项垂直间距：不小于 `8px`；
- 表格必须有独立滚动容器，不能撑破页面；
- 长 path/hash/错误使用 monospace 并允许断行；
- 正文不要使用过小的 9–10px 字号；只允许来源、时间和 hash 使用小字号。

不要用大量相同边框卡片制造“卡片墙”。层级优先用留白、字号、背景和局部分隔。

---

## 6. AI 判断 Tab 重构

### 6.1 当前问题

`DecisionReportTab.vue` 目前按模型 Schema 的字段顺序渲染：

- 市场证据按钮全部展示；
- 每个 Ranking 同时展示 thesis、期权过滤、风险、缺失字段、失效条件和证据；
- 所有 Ranking 卡片纵向完全展开；
- 重要判断与辅助审计信息视觉权重接近。

这使页面更像“结构化模型响应浏览器”，不是决策报告。

### 6.2 新的信息层级

AI Tab 分为四层：

```text
1. 当前结论
2. 关键变化与风险
3. 关注标的列表
4. 单标的详情 / 证据
```

#### 第一屏：当前结论

```text
┌────────────────────────────────────────────────────────────┐
│ 市场：选择性 Risk-on          置信度 65%                  │
│ CTA：权益小幅增配              波动率：多数折价             │
│                                                            │
│ 一句话判断：趋势仍偏多，但参与度不足，软件弱于半导体……    │
│                                                            │
│ [8 个关注标的] [3 个主要风险] [2 个数据缺口]               │
└────────────────────────────────────────────────────────────┘
```

这里不展示 Evidence path、模型字段名或完整缺失字段。

#### 第二层：变化与风险

使用两个并排列表：

- “支持当前判断”；
- “反对/可能改变判断”。

每边最多 4 条，更多内容进入“查看全部证据”抽屉。

CTA、IV/HV、Gamma、事件若构成关键结论，使用独立状态行，而不是混入段落。

#### 第三层：关注标的列表

默认使用紧凑可排序表，不使用 8 张完全展开的大卡片：

| Rank | Symbol | 当前状态 | Score | 关键理由 | IV/HV | Gamma | 风险数 |
|---:|---|---|---:|---|---|---|---:|

支持：

- 按 Rank、Score、主题、波动率状态过滤；
- `watch / observe / avoid` 标签；
- 点击行打开详情抽屉；
- 当前选中行写入 `?symbol=NVDA`。

手动现状分析只叫“关注标的”，不要写“买入候选”。

#### 第四层：标的详情抽屉

抽屉内部使用 Tab：

```text
判断 | 技术 | 期权 | 风险与证据
```

“判断”只展示 thesis、当前 action、改变条件。

“技术”展示价格、MA、MACD、相对强弱、成交量，不重复整份市场报告。

“期权”展示 IV/HV、Gamma regime、Flip、Expected Move、墙；如果数据完整，嵌入小型 Gamma 图。

“风险与证据”再展示：

- risks；
- missing_fields；
- invalidation_conditions；
- Evidence Reference；
- 跳转到技术报告对应位置。

原始 Evidence path 不能作为主文案按钮。按钮文案用 observation，path 放 tooltip/详情中。

### 6.3 正式报告与手动报告的区别

正式盘前报告可以出现：

- forecast；
- if_cash / if_held；
- 方向、概率和预期区间。

手动 current-state 报告不出现：

- 正式预测评分；
- 买卖指令；
- 空仓/持仓动作；
- “执行就绪”作为主要状态。

复盘报告单独显示预测 vs 实际，不与当前状态 Ranking 混排。

---

## 7. 技术报告重构

### 7.1 二级 Tab 保留，但内容重新定义

```text
总览 | 个股技术 | 期权结构 | 事件与数据质量
```

只渲染当前 Tab。不要在“总览”之后继续把其他 Tab 的完整数据铺在同一页面。

### 7.2 总览：结论先于明细

总览首屏分为：

1. 市场状态条；
2. 当日关键变化；
3. CTA 压力；
4. 波动率异常；
5. 数据质量告警。

推荐结构：

```text
市场状态
QQQ +0.95% · 高于 MA20/50 · 成交量偏低

┌──────────┬──────────┬──────────┬──────────┐
│ 趋势     │ 广度     │ CTA      │ 波动率   │
│ 偏多     │ 分化     │ 小幅买入 │ 整体折价 │
└──────────┴──────────┴──────────┴──────────┘

今日最重要的变化
• 半导体显著强于软件
• HYG/UUP CTA 压力上升
• LITE/COHR IV 明显低于 HV30
```

不要把 12 个 CTA 代理的整张表放在首屏。首屏只展示：

- 最大买入压力 3 个；
- 最大卖出压力 3 个；
- 组合 Net/Gross；
- “查看全部 CTA”展开区。

### 7.3 市场与跨资产图表

需要新增或完善：

1. 核心 ETF 横向表现条：SPY、QQQ、SMH、IGV；
2. `pre_market → current` dumbbell/slope 图；
3. 跨资产热力图：权益、信用、久期、美元、黄金、原油；
4. CTA pressure 横向条形图，零轴居中；
5. 若只有 current_state，没有盘前配对数据，显示“当前快照”，不要画伪变化线，也不要用“盘前→收盘前”标题。

图表必须有：

- 明确单位；
- 数据截止时间；
- hover/tooltip 或可读标签；
- 正负零轴；
- 空数据状态。

### 7.4 个股技术

默认显示“异常与领先者”，不是 27 个标的全部展开。

顶部提供：

```text
[领先] [接近突破] [趋势转弱] [量价异常] [全部]
```

主体为可排序矩阵：

| Symbol | 主题 | 当日 | 趋势 | MA20/50/200 | MACD | 量比 | RS vs QQQ | RV20 | 状态 |

点击后进入详情抽屉，详情必须有辅助图表：

- 价格 + MA20/50/200；
- 成交量柱 + 20 日均量；
- MACD DIF/DEA/Histogram；
- 相对 QQQ 强弱曲线；
- Bollinger 带；
- 波动率 10/20/60 日对比。

如果冻结数据只有汇总指标而没有绘图序列，先明确显示“仅有指标摘要”，不要用单点伪装成曲线。后端需补充 compact chart series 时另建任务。

### 7.5 期权结构：必须复用已有好设计

开发工具中的 `OptionsPanel.vue` 已经有两块合格设计：

1. **现价 Gamma 曲线与 Flip**（Spot Gamma Profile）；
2. **行权价结构图**（Exposure Map）。

技术报告不能继续只展示简单 DEX/GEX 表格。必须把这两个视图提取为共享组件，供开发工具和报告共同使用。

建议拆分：

```text
components/options/SpotGammaProfileChart.vue
components/options/StrikeExposureMap.vue
components/options/OptionStructureSummary.vue
components/options/OptionExpirationSelector.vue
```

禁止复制两份 SVG 计算逻辑。坐标计算、Gamma Flip、重点 Strike、墙、Max Pain、现价标记应只有一套实现。

期权 Tab 的结构：

```text
Symbol Tabs
Expiration Selector

摘要：Spot / Expected Move / IV / HV30 / IV-HV / Gamma Regime

[Gamma 曲线] [行权价结构] [IV/HV] [明细表]
```

其中第三层可使用局部 Tab，默认展示 Gamma 曲线，而不是先展示原始聚合表。

行权价原始表默认折叠，用户主动点击“查看行权价明细”才展开。

### 7.6 IV/HV 可视化

新增：

- IV 与 HV30 并排柱；
- `IV-HV spread` 零轴图；
- 折价/匹配/溢价状态标签；
- 若有历史序列，再显示 percentile 和趋势；没有历史序列时不要画 percentile 图。

必须说明：IV/HV 是波动率定价，不是方向信号。

### 7.7 事件与数据质量

事件时间轴和数据质量合并为最后一个二级 Tab：

- 即将发生；
- 已发生、结果已返回；
- 结果缺失；
- 数据质量 warnings/blocking errors；
- 来源和冻结时间。

普通告警不要占据报告首屏。只有 blocking error 或影响结论的缺失项才在总览显示。

---

## 8. 原始数据与开发工具边界

研究报告中允许出现：

- “查看证据”；
- “查看完整指标”；
- “数据来源”；
- “打开开发工具中的原始快照”。

研究报告中不允许默认出现：

- 完整 JSON；
- 20+ 列的原始表；
- 所有 Evidence path；
- 每个模型内部字段；
- 所有缺失字段重复出现在每张卡片上。

开发工具继续负责：

- 原始快照；
- Run/Step payload；
- 完整期权链聚合；
- Trace 和模型返回；
- 数据采集故障。

报告只提供深链接，不复制开发工具的调试信息。

---

## 9. 组件与代码改造建议

优先改造文件：

```text
frontend/src/views/ResearchReportView.vue
frontend/src/components/research/ReportHeader.vue
frontend/src/components/research/TechnicalReportTab.vue
frontend/src/components/research/DecisionReportTab.vue
frontend/src/components/OptionsPanel.vue
frontend/src/styles.css
frontend/src/router/index.ts
frontend/src/components/AppShell.vue
```

新增建议：

```text
frontend/src/views/UniverseSettingsView.vue
frontend/src/components/settings/UniverseTable.vue
frontend/src/components/settings/InstrumentConfigDrawer.vue
frontend/src/components/research/MarketStateHero.vue
frontend/src/components/research/AttentionTable.vue
frontend/src/components/research/InstrumentDetailDrawer.vue
frontend/src/components/research/CtaPressureChart.vue
frontend/src/components/research/CrossAssetHeatmap.vue
frontend/src/components/options/SpotGammaProfileChart.vue
frontend/src/components/options/StrikeExposureMap.vue
```

CSS 不再继续在 `styles.css` 无限追加。新组件优先使用 scoped style；全局只保留 token、排版、按钮、状态和页面容器。

建议补充统一 token：

```css
--content-max-width
--section-gap
--card-padding
--text-primary
--text-secondary
--positive
--negative
--warning
--chart-grid
```

---

## 10. 实现阶段与每阶段验收

### 阶段 A：报告骨架和信息折叠

- [ ] 公共页头简化，运行信息进入抽屉。
- [ ] 一级 Tab sticky 且写入 URL。
- [ ] AI Ranking 改为紧凑表格。
- [ ] 单标的详情使用抽屉和内部 Tab。
- [ ] 风险、缺失项、证据默认不全部展开。
- [ ] current-state 与正式预测文案正确隔离。

验收：打开 AI 报告时，首屏能看到市场判断、主要风险和关注标的，不需要先滚过模型元数据。

### 阶段 B：技术报告总览

- [ ] 总览改为结论优先。
- [ ] CTA 只默认展示极值和组合摘要。
- [ ] 增加跨资产热力图、CTA 零轴条形图。
- [ ] current-state 不再显示虚假的“盘前→收盘前”。
- [ ] 全部原始表进入折叠区。

验收：用户无需阅读整张 CTA 表就能指出最大买卖压力和市场风格。

### 阶段 C：共享期权图表

- [ ] 从 `OptionsPanel.vue` 提取 Gamma 曲线共享组件。
- [ ] 提取行权价结构图共享组件。
- [ ] 开发工具和技术报告使用同一组件。
- [ ] Symbol、到期日和视图状态写入 URL。
- [ ] 原始行权价表默认折叠。

验收：同一 Dataset、Symbol、Expiration 在开发工具与报告中的 Gamma/Strike 图数据完全一致。

### 阶段 D：个股技术图表

- [ ] 个股矩阵提供状态过滤和排序。
- [ ] 单标的详情抽屉。
- [ ] 有序列时展示价格/MA、Volume、MACD、RS、Bollinger。
- [ ] 无序列时显示明确空态，不画伪图。

验收：用户从列表进入 NVDA 详情不离开报告，并能看到关键趋势而非一组散落数值。

### 阶段 E：Universe 设置

- [ ] 统一配置模型与版本表。
- [ ] 配置读写、校验和影响预览 API。
- [ ] `/settings/universe` 列表、过滤、抽屉和差异确认。
- [ ] Run 冻结 universe 版本。
- [ ] 历史报告显示使用的 universe 版本。

验收：新增或停用一个标的后，下一轮采集按新版本执行，历史报告保持不变。

---

## 11. 通用验收标准

### 视觉与交互

- 1440px、1024px、768px 三个宽度无横向页面溢出。
- 表格只在自身容器横向滚动。
- 首屏不出现完整 JSON、长 hash 或 20 列表格。
- Tab、filter、drawer 有清晰选中状态。
- 所有图表都有空态、单位、图例和截止时间。
- 键盘可操作 Tab、表格行和抽屉关闭按钮。

### 数据正确性

- UI 不重新计算业务口径；Gamma/IV-HV/CTA 使用后端冻结值。
- 图表值与原始技术报告字段一致。
- 手动报告不显示正式交易决策字段。
- Report/Dataset 明确记录 universe version。
- Evidence 跳转能定位到对应技术模块。

### 性能

- 默认只渲染当前一级和二级 Tab。
- 大表、图表和详情抽屉按需渲染。
- 切换已访问 Tab 不重复请求相同 endpoint。
- 27–100 个标的列表保持可用；必要时使用虚拟滚动。

### 测试

至少新增：

- Universe 配置验证和版本冲突测试；
- current_state/正式报告的条件渲染测试；
- AI Ranking 表与详情抽屉测试；
- Gamma 共享组件快照/数据映射测试；
- current_state 无 paired observation 时的标题和空态测试；
- URL Tab/Symbol/Expiration 状态恢复测试；
- 技术报告缺少 phase 时不崩溃的回归测试。

## 12. Luna 提交方式

不要一次提交一大坨难以审查的修改。按阶段 A–E 分批，每阶段提供：

1. 修改文件清单；
2. 页面截图：桌面 + 窄屏；
3. 使用的真实 report_id / dataset；
4. 新增测试与结果；
5. 尚未实现或数据不足的部分；
6. 与本文不一致的取舍及理由。

Luna 开始前先回复阶段 A 的组件拆分和页面线框；确认后再写代码。
