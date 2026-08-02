# Urus 框架开发需求

> 本文用于交给另一个开发对话实施 Urus 的第一步框架。
> 当前只搭建可运行、可测试、可扩展的应用骨架，不实现真实市场采集、期权计算、Anomalo 调用或交易决策。

## 1. 项目目标

Urus 是一个股票分析与决策辅助系统。完整业务流程最终为：

1. **步骤 1A**：程序采集大盘信息。
2. **步骤 1B**：重要宏观事件发布后，按需调用 Anomalo agent 生成一两句话摘要。
3. **步骤 2**：程序采集并计算期权信息。
4. **步骤 3A**：程序采集个股行情和已有慢变量。
5. **步骤 3B**：出现新财报、指引或重大公司事件时，按需调用 Anomalo agent 摘要。
6. **步骤 4**：程序整理已有数据并向决策 AI 寻求判断。
7. **步骤 5**：程序校验结果并输出给前端。

本次开发只建设能够承载上述流程的框架。框架完成并验收后，再按照章节 12 的顺序逐阶段实现，不允许在本次任务中提前填充真实业务。

策略与数据需求的讨论原文位于同目录的 `strategy-discussion.md`。开发者可以阅读它理解背景，但本次实施范围以本文为准。

## 2. 技术形态

采用前后端分离架构：

- 后端：Python + FastAPI。
- 前端：Vue 3 + Vite + TypeScript。
- 前端和后端分别拥有依赖、构建命令和运行进程。
- 可以放在同一个仓库的 `backend/` 与 `frontend/` 目录，但必须能够独立启动和部署。
- 后端提供版本化 REST API；前端只通过 API 访问后端，不直接读取数据库或调用 Moomoo、Anomalo。

本次不引入微服务、消息队列、Kubernetes或复杂插件平台。先保持单一 FastAPI 应用和单一 Vue 应用。

## 3. 目录要求

建议建立以下结构；允许根据工具惯例轻微调整，但职责边界不得混淆：

```text
Urus/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI routers
│   │   ├── core/                # 配置、日志、异常、生命周期
│   │   ├── models/              # 持久化模型
│   │   ├── schemas/             # API/Pydantic schema
│   │   ├── repositories/        # 数据访问
│   │   ├── services/            # 应用服务
│   │   ├── workflows/           # 五步流程编排和步骤接口
│   │   ├── integrations/        # 未来 Moomoo、Anomalo、网页来源适配器
│   │   └── main.py
│   ├── tests/
│   ├── alembic/                 # 若采用 SQLAlchemy/Alembic
│   ├── pyproject.toml
│   └── README.md
├── frontend/
│   ├── src/
│   │   ├── api/                 # 后端 API client
│   │   ├── components/
│   │   ├── views/
│   │   ├── stores/
│   │   ├── types/
│   │   ├── router/
│   │   └── main.ts
│   ├── tests/
│   ├── package.json
│   └── README.md
├── docs/                        # 后续架构/API说明
├── .env.example
├── README.md
├── development-requirements.md
└── strategy-discussion.md
```

路由层不得直接包含行情或决策业务。外部系统调用必须通过 `integrations/` 中的适配器接口进入。

## 4. 后端框架要求

### 4.1 FastAPI 应用

必须具备：

- 应用工厂或清晰的 `main.py` 入口。
- `/api/health` 健康检查。
- `/api/version` 返回应用版本和 API schema 版本。
- OpenAPI 文档可用。
- 配置化 CORS，仅允许环境变量指定的前端来源。
- 统一错误响应，不向前端泄露堆栈和密钥。
- 启动、关闭生命周期钩子。
- 日志至少包含时间、level、请求路径；涉及运行任务时带 `run_id` 和 `snapshot_id`。

### 4.2 配置

使用环境变量和配置类，至少预留：

- `APP_ENV`
- `APP_HOST`
- `APP_PORT`
- `DATABASE_URL`
- `CORS_ORIGINS`
- `ENABLED_SYMBOLS`，开发默认严格为 `QQQ,INTC`
- `MOOMOO_ENABLED=false`
- `ANOMALO_ENABLED=false`
- `ANOMALO_BASE_URL`
- `ANOMALO_TIMEOUT_SECONDS`

不得提交真实密钥、账户信息或本机专用地址。`.env.example` 只放安全示例值。

### 4.3 持久化

框架需要持久保存运行记录和步骤状态，以便刷新页面后仍能查看。

建议采用 SQLAlchemy + Alembic：

- 本地开发默认 SQLite。
- `DATABASE_URL` 可切换到 PostgreSQL，不在业务代码中依赖 SQLite 特性。
- 本次只创建最小模型，不设计完整行情数据库。

最小实体：

#### Run

- `id`
- `run_type`：`pre_market` 或 `pre_close`
- `status`：`pending/running/succeeded/partial/failed`
- `started_at`
- `completed_at`
- `cutoff_time`
- `snapshot_id`，可以在框架阶段先为空，运行成功时生成
- `error_message`

#### StepRun

- `id`
- `run_id`
- `step_code`：`1a/1b/2/3a/3b/4/5`
- `status`：`pending/running/succeeded/skipped/failed`
- `started_at`
- `completed_at`
- `summary`
- `error_message`

#### Snapshot

- `id`
- `run_id`
- `schema_version`
- `cutoff_time`
- `created_at`
- `quality_status`
- `payload`：框架阶段只保存小型 mock/read-model JSON，不保存真实大规模行情

数据库迁移必须可从空数据库执行。

## 5. 五步工作流骨架

### 5.1 步骤接口

在 `workflows/` 中为以下步骤建立统一但简单的接口：

- `MarketCollectorStep`：1A
- `MarketEventSummaryStep`：1B
- `OptionsCollectorStep`：2
- `InstrumentCollectorStep`：3A
- `InstrumentEventSummaryStep`：3B
- `DecisionStep`：4
- `OutputStep`：5

每个步骤接收运行上下文，返回步骤结果。至少支持：

- 成功
- 跳过
- 失败
- 简短说明
- 小型结构化 payload

不要为未来所有可能性创建抽象工厂。只需要让步骤可以被独立替换和测试，外部依赖可以注入 mock adapter。

### 5.2 框架阶段行为

本次所有步骤均使用 mock/stub：

- 1A 返回 QQQ 的示例市场卡，不请求真实行情。
- 1B 默认 `skipped`；可以通过测试参数模拟“今天有事件”并返回固定摘要。
- 2 返回固定的期权占位状态，不计算真实 IV/GEX。
- 3A 返回 INTC 的示例个股卡，不请求真实行情。
- 3B 默认 `skipped`；可以模拟“发现新财报”。
- 4 返回固定且明确标记为 mock 的决策结果，不调用任何 AI。
- 5 组合前述结果，生成前端 read model。

所有 mock 数据必须带 `is_mock=true`，前端必须明显显示“模拟数据”。绝不能让框架占位数据看起来像实时市场事实。

### 5.3 执行方式

框架阶段支持手动触发一次运行：

- `POST /api/runs`
- 请求指定 `run_type=pre_market` 或 `pre_close`
- 默认按 `1a → 1b → 2 → 3a → 3b → 4 → 5` 执行
- 返回新建的 `run_id`

可以同步完成这个轻量 mock 流程，也可以使用 FastAPI 后台任务；不要在本次引入 Celery、Redis或独立调度服务。

定时每天运行两次属于后续阶段。本次只预留调度接口或服务边界，不启动真实定时任务。

### 5.4 条件步骤

必须体现以下语义：

- 1B 只有重要宏观事件已经发布时才运行，否则为 `skipped`。
- 3B 只有发现新财报、指引或重大公司事件时才运行，否则为 `skipped`。
- `skipped` 是正常状态，不属于失败。
- 框架阶段通过 mock 输入测试条件分支。

## 6. 后端 API 最小集合

至少提供：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/version` | 应用/API版本 |
| POST | `/api/runs` | 手动启动一次 mock 运行 |
| GET | `/api/runs` | 最近运行列表 |
| GET | `/api/runs/{run_id}` | 运行及各步骤状态 |
| GET | `/api/snapshots/{snapshot_id}` | 获取冻结的 mock snapshot |
| GET | `/api/snapshots/{snapshot_id}/frontend` | 获取前端 read model |
| GET | `/api/watchlist` | 返回配置中的开发白名单 |

`POST /api/runs` 必须拒绝请求 `QQQ,INTC` 以外的真实或模拟标的扩展，除非以后显式修改配置。当前关注列表其他标的不进入框架开发的数据调用。

API 返回使用稳定的 Pydantic schema。前端类型应从 OpenAPI 生成，或至少通过一个集中 API client 管理；不得在多个组件中各自手写不同字段解释。

## 7. 前端框架要求

### 7.1 基础技术

- Vue 3 Composition API。
- Vite。
- TypeScript。
- Vue Router。
- Pinia 或同等轻量状态管理。
- 一个集中式后端 API client，base URL 由环境变量配置。

本次不指定大型 UI 组件库。`sibling-project` 当前主要使用原生 Vue 与手写 CSS，Urus 框架也优先保持轻量；若开发者选择引入组件库，必须说明理由，且不得让组件库配置占据主要开发工作。

### 7.2 初版视觉基线：参考兄弟项目 sibling-project

框架阶段的页面风格参考只读兄弟项目：

- `sibling-project/frontend/src/App.vue`
- `sibling-project/frontend/src/styles.css`

参考的是设计语言和交互密度，不复制 RAG 的项目、文档、检索等业务组件。初版建议沿用：

- 深橄榄黑画布、略浅的卡片表面、暖白主文字和低饱和灰绿色次要文字。
- 浅橄榄绿强调色，用于选中、主按钮、成功和重点状态；错误继续使用克制的暖红色。
- 标题和正文使用衬线字体栈，时间、状态、ticker、分数和技术元数据使用等宽字体。
- 约 1080px 的居中主内容宽度、充分留白、细边框、8–10px左右圆角。
- 简洁 topbar、页面说明、工作区、tab、卡片和状态行；避免传统后台模板式的密集侧边栏。
- hover、selected、focus状态保持轻微颜色和边框变化，不添加无意义的大幅动画。
- 在窄屏下改为单列，按钮和状态行自然换行。

可将以下 sibling-project CSS变量作为临时起点，但应在 Urus 自己的样式文件中重新定义，不让两个项目产生运行时依赖：

```css
:root {
  --canvas: #171914;
  --surface: #20231e;
  --surface-raised: #292d26;
  --surface-selected: #343a2e;
  --ink: #f1eee4;
  --soft-ink: #d5d3c8;
  --muted: #a8aca0;
  --line: #4a5147;
  --line-soft: #353b34;
  --accent: #c6d58e;
  --success: #b9d998;
  --danger: #ff9c8d;
}
```

这只是框架阶段的视觉占位基线。页面信息架构稳定后会另行讨论和修改整体风格，因此本次不做像素级复刻、品牌设计、复杂图表美化或大量响应式细节。业务组件不得和这些颜色或布局硬耦合，以便以后替换主题。

### 7.3 最小页面

#### Dashboard

- 显示后端连接状态。
- 显示最近一次运行类型、状态、截止时间和 mock 标记。
- 显示步骤 `1A/1B/2/3A/3B/4/5` 的状态时间线。
- 显示 QQQ mock 大盘卡。
- 显示 INTC mock 个股卡。
- 显示事件摘要占位、期权占位和 AI 决策占位。
- 显示数据质量与错误信息。

#### Runs

- 最近运行列表。
- 点击进入单次运行详情。

#### Run Detail

- 展示单次运行的步骤状态、耗时、跳过原因和错误。
- 展示对应 snapshot/read model。

### 7.4 前端边界

- 前端只负责显示、筛选和用户触发，不重新计算布林带、GEX或账户风险。
- 缺失值显示为不可用，不显示成0。
- mock数据必须始终有显眼标识。
- API错误有可读提示，不能只写入浏览器console。
- 页面刷新后通过后端恢复运行和snapshot状态。

## 8. Anomalo 接口预留

Anomalo 已有：

- `POST /api/chat`
- `POST /api/chat/stream`
- `WS /ws/chat/{session_id}`

第一阶段实际落地时只计划使用 `POST /api/chat`。本次框架开发要求：

- 在 `integrations/anomalo.py` 或等价位置定义 adapter 接口。
- 提供 disabled/mock 实现。
- 不进行真实 HTTP 调用。
- 配置 `ANOMALO_ENABLED=false` 时不会访问网络。
- 为以后请求中的 `session_id`、`message` 以及响应中的 `final_text` 预留类型。
- 1B、3B和4未来必须使用不同 session ID。

由于 Anomalo 当前没有完整鉴权，真实接入时只允许本机或可信内网；非本机部署需先增加认证或反向代理。本次不负责实现该安全层。

## 9. Moomoo 与外部数据边界

本次禁止调用真实 Moomoo 或任何付费/受限行情接口。

- `MOOMOO_ENABLED` 默认且强制为 `false`。
- 可以定义 Moomoo adapter protocol 和 mock 实现。
- 不安装或连接 OpenD，除非仅为类型导入且测试完全离线；优先推迟到阶段1A/3A。
- 不消耗股票、历史K线、期权订阅或期权历史额度。
- 不抓取网页、不调用搜索、不下载财务文件。

开发白名单固定为：

- QQQ：代表大盘和未来期权流程测试。
- INTC：代表个股流程测试。

## 10. 日志、错误与可观察性

- 每次运行生成 `run_id`。
- 成功生成输出时再关联 `snapshot_id`。
- 每个步骤记录开始、完成、跳过或失败。
- 单个可选步骤失败不一定使整个运行失败；框架需支持 `partial`。
- 关键步骤失败时步骤5仍生成错误read model，前端可以展示发生了什么。
- 不记录密钥、完整账户信息或未来可能包含的敏感prompt。
- 本次不接入Sentry、Prometheus等外部服务，只预留清晰日志边界。

## 11. 测试和验收标准

### 11.1 后端测试

至少覆盖：

- 健康检查成功。
- 空数据库可以迁移并启动。
- 可以创建 `pre_market` mock run。
- 可以创建 `pre_close` mock run。
- 1B/3B可以正常显示`skipped`。
- 模拟事件时1B或3B可以进入`succeeded`。
- 步骤状态按顺序保存。
- 运行完成后可以取得snapshot和前端read model。
- 非QQQ/INTC标的被拒绝。
- Anomalo/Moomoo关闭时不会发起外部请求。
- 一个mock步骤失败时运行状态和错误可被前端读取。

### 11.2 前端测试

至少覆盖：

- 前端可以构建。
- Dashboard可以读取并显示mock read model。
- 步骤状态时间线正确显示成功、跳过和失败。
- mock标记可见。
- 后端不可用时显示错误状态。
- 缺失字段不会显示为0。

### 11.3 整体验收

框架完成时应能：

1. 分别启动FastAPI和Vue开发服务器。
2. 从前端手动触发一次mock运行。
3. 后端依次执行五步流程中的七个步骤代码。
4. 前端看到运行状态、QQQ、INTC、条件步骤、mock AI结果和质量信息。
5. 刷新页面后仍能恢复该运行。
6. 全程没有真实Moomoo、Anomalo或网页调用。
7. 后端测试、前端测试和前端生产构建均通过。

## 12. 后续阶段顺序

框架验收后，每个阶段单独讨论、实现和验收：

1. **阶段1A**：大盘程序采集，先用QQQ验证。
2. **阶段1B**：事件日网页采集与Anomalo一两句话摘要。
3. **阶段2**：期权数据，先用QQQ验证。
4. **阶段3A**：个股行情和技术特征，先用INTC验证。
5. **阶段3B**：INTC财报/公司事件检测与Anomalo摘要。
6. **阶段5-read-model**：用真实已采集数据完善前端输出。
7. **阶段4**：只有确认前述阶段实际能稳定提供哪些高质量数据后，才设计和实现决策AI证据包、prompt与返回格式。
8. **阶段5-final**：程序风险校验、最终前端合并和复盘闭环。
9. **扩容阶段**：经确认后从QQQ/INTC逐批启用完整关注列表。

阶段4不得提前。决策AI的输入必须由前面真实落地的数据能力决定，不能先设计一个理想化的大而全prompt，再倒逼系统收集低价值数据。

## 13. 本次明确不做

- 真实行情、财务、新闻或网页采集。
- 真实Moomoo连接和额度使用。
- 真实Anomalo调用。
- IV曲面、概率分布、GEX或其他期权计算。
- 完整布林带和个股指标实现；框架只显示mock字段。
- 决策AI prompt和真实输出schema。
- 自动定时运行。
- 自动下单、模拟盘或实盘交易。
- 用户登录、权限管理和多租户。
- 云部署、容器编排和生产监控。
- 完整视觉设计和移动端适配。

## 14. 交付要求

另一个开发对话完成后需要交付：

- 可运行的`backend/`。
- 可运行的`frontend/`。
- 数据库迁移。
- mock五步流程。
- 最小REST API。
- 最小Dashboard、Runs和Run Detail页面。
- 后端和前端测试。
- 根目录README，包含安装、启动、测试、构建和环境变量说明。
- 一份简短的实现说明，列出实际目录、关键技术选择、已知限制和下一阶段接入点。

不要修改`strategy-discussion.md`中的策略结论；如实现与本文冲突，应先报告冲突，不自行扩大范围。
