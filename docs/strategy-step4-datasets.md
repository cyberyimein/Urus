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

## 后续使用约定

- `pending_events`：可以研究市场、技术和期权结构，但策略 Agent 必须知道事件证据尚未完成。
- `ready`：只有 1B、3B 都成功完成并且事件证据满足策略输入要求时才使用。
- 数据集不随新的普通运行自动覆盖；每次捕获使用显式 `dataset_key`，保留独立研究样本。
