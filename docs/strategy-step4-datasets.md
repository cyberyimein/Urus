# Step 4 strategy research datasets

Step 4 研究使用独立的 `strategy_research_datasets` 数据集，不直接依赖前端当前显示的
“最近一次运行”。数据集保存源运行的快照、每个步骤的原始 payload 和当时的事件台账，
便于策略 Agent 重复研究并保留审计上下文。

## 21:30 基线

2026-08-03 21:30（Asia/Tokyo）对应：

- `source_run_id=317cc925-90e7-425a-a8ed-69066a646823`
- `source_snapshot_id=774a9824-7582-48dc-8862-6228de929496`
- `dataset_key=step4-strategy-2026-08-03-2130`
- `status=pending_events`

保存命令：

```bash
cd backend
.venv/bin/python scripts/capture_strategy_dataset.py \
  --run-id 317cc925-90e7-425a-a8ed-69066a646823 \
  --dataset-key step4-strategy-2026-08-03-2130 \
  --label 'Step4 strategy AI baseline · 2026-08-03 21:30'
```

这次快照的 1A、2、3A 和最终 snapshot 已保存；1B、3B 当时是 `skipped`，因此数据集明确
标记为 `pending_events`，不能作为事件完整的最终策略样本。当前保存了 39 条事件台账记录，
但事件结果是否完整仍以之后启用 1B/3B 的运行结果为准。

## 2026-08-04 21:30 新基线

今晚 21:30（Asia/Tokyo）的 `pre_market` 采集已单独捕获，且 1B/3B 事件步骤均已完成：

- `source_run_id=2082b9de-e693-4327-b4d8-a2d1025d15d3`
- `source_snapshot_id=9130fe07-ca06-40ff-86c9-d2784cba07b0`
- `dataset_id=ebd77e90-bb99-40f1-b093-2c6cebd16f7e`
- `dataset_key=step4-strategy-2026-08-04-2130`
- `status=ready`
- 事件台账记录：47 条

独立 JSON 备份：

```text
backend/data/strategy_research/step4-strategy-2026-08-04-2130.json
```

文件大小约 19.8 MB，SHA-256 为
`a010435a168bc9b31d87d63165c92b7800acc26b1bf35809ef921533d399d7d8`。
后续 AI 调用应使用这份冻结数据，不要重新采集同一时点。

## 独立 JSON 备份

SQLite 之外还导出了一份独立 JSON：

```text
backend/data/strategy_research/step4-strategy-2026-08-03-2130.json
```

当前文件约 17 MB，包含完整数据集 payload 和 `content_sha256` 校验摘要。重新导出任意数据集：

```bash
cd backend
.venv/bin/python scripts/export_strategy_dataset.py \
  --dataset-key step4-strategy-2026-08-03-2130
```

该目录属于本机运行数据并被 Git 忽略，SQLite 重置不会删除它；如果需要防止整个工作区或磁盘
损坏，还应把 JSON 复制到独立磁盘或对象存储。

## 4B 盘前/收盘前配对数据

4B 策略研究另外保留同一交易日的两次行情观察：盘前一小时的 `pre_market` 与收盘前一小时的
`pre_close`。两次观察放在同一个独立 JSON 中，保留各自的 snapshot、步骤原始 payload、运行元数据，
并附带捕获时的事件台账。当前 2026-08-03 配对为：

- `pre_market`: `317cc925-90e7-425a-a8ed-69066a646823`（21:30 JST）
- `pre_close`: `41b17624-df2a-4841-836f-4b0086b95b4c`（收盘前一小时）

生成配对文件：

```bash
cd backend
.venv/bin/python scripts/capture_strategy_pair.py \
  --pre-market-run-id 317cc925-90e7-425a-a8ed-69066a646823 \
  --pre-close-run-id 41b17624-df2a-4841-836f-4b0086b95b4c \
  --dataset-key stage4b-premarket-preclose-2026-08-03 \
  --label 'Stage4B strategy pair · 2026-08-03'
```

输出文件为 `backend/data/strategy_research/stage4b-premarket-preclose-2026-08-03.json`，
与单次 Step 4 数据集分开保存。

在线工作流采用同一配对契约但不依赖这个手工 JSON：`pre_market` 快照先正常持久化；同一美东
交易日的 `pre_close` 到达 Step 4 时，程序查询最近的 `pre_market` 快照，并将其与当前内存结果
压缩为冻结 Decision Packet。若当日没有匹配的 `pre_market`，Step 4 显示 `waiting_for_pair` 且不
调用模型。手工 JSON 继续作为独立研究备份与离线重放输入。

## 4B 三阶段测试夹具（2026-08-03）

为验证新的三阶段 Agent 工作流，已将同一美东交易日的三份真实快照导出为独立测试夹具：

- `pre_market`: `317cc925-90e7-425a-a8ed-69066a646823`（21:30 JST）
- `pre_close`: `41b17624-df2a-4841-836f-4b0086b95b4c`（收盘前一小时）
- `post_close_review`: `80d126e6-7396-490f-9274-bddc97540baf`（2026-08-04 06:59 JST，真实
  Moomoo/OpenD 盘后快照，payload 中 `market.session=afterhours`）

夹具文件：

```text
backend/data/strategy_research/stage4b-daily-2026-08-03-test.json
```

生成命令：

```bash
cd backend
.venv/bin/python scripts/capture_stage4b_daily_dataset.py \
  --pre-market-run-id 317cc925-90e7-425a-a8ed-69066a646823 \
  --pre-close-run-id 41b17624-df2a-4841-836f-4b0086b95b4c \
  --post-close-run-id 80d126e6-7396-490f-9274-bddc97540baf \
  --trading-date 2026-08-03
```

文件顶层明确标记 `test_fixture=true`，并保留 `content_sha256`。它只用于工作流验证、离线重放
和策略研究，不会改变 SQLite 中的原始运行，也不代表该批次可以作为生产的 `post_close_review`
运行。夹具中的 `run_type` 与真实记录保持不变；程序应以 `fixture_phase` 和
`market.session` 识别其测试阶段，避免把历史 `pre_market` 记录误当作生产收盘运行。

验证记录：使用 fixture provider 的三阶段结构化冒烟已成功生成三个
`urus.ai_decision_report.v3` 报告；真实 OpenRouter 收盘复盘调用在市场、题材并行节点后进入
综合节点，等待超过五分钟仍未返回，随后在临时 SQLite 中止。该次未写入正式数据库，属于模型
服务延迟而非盘后快照或夹具校验失败。

Stage 4B 的 AI 不应直接读取这个大文件。使用
`scripts/build_stage4b_decision_packet.py` 生成精简且供应商无关的决策包；字段取舍、两个分析
Skill 和供应商边界见 [Stage 4B AI 决策设计](stage4b-ai-decision.md)。

## 后续使用约定

- `pending_events`：可以研究市场、技术和期权结构，但策略 Agent 必须知道事件证据尚未完成。
- `ready`：只有 1B、3B 都成功完成并且事件证据满足策略输入要求时才使用。
- 数据集不随新的普通运行自动覆盖；每次捕获使用显式 `dataset_key`，保留独立研究样本。
