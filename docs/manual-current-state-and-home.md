# 手动即时分析与决策主页

## 产品分层

- `/`：投资研究驾驶舱，展示最新正式判断、当前 CTA/IV-HV/质量状态并发起手动分析。
- `/research`：研究中心，归档正式日循环、手动报告与冻结数据集。
- `/operations`：采集、步骤状态和错误诊断工具。

## 手动即时分析

`POST /api/analysis/runs` 异步创建 `run_type=manual_analysis` 工作流。它完整执行
1A/1B/2/3A/3B，冻结独立 Dataset，生成技术报告，然后运行一次
`decision_phase=current_state` 的 AI 现状分析。该模式把冻结后的市场、主题、CTA 和 IV/HV
上下文交给一个 `Current State` 模型节点，不运行正式周期中的 Market → Themes → Synthesis
多模型链。前端通过 `/analysis/runs/:runId` 轮询步骤状态。

固定语义字段：

```json
{
  "trigger_type": "manual",
  "analysis_mode": "current_state",
  "report_scope": ["technical_report", "ai_state_analysis"],
  "official_cycle": false,
  "eligible_for_scoring": false,
  "updates_official_cta_state": false
}
```

这些字段同时进入冻结 packet、Technical Report、AI Session policy、AI report 和前端 read
model。正式复盘只按 `decision_phase=pre_market` 查询同日预测，因此 `current_state` 会话不能污染
预测评分或成为正式父报告。

AI 失败时，已创建的 Session 仍保存确定性 Technical Report、错误和 Trace。用户可以从进度页
查看采集详情，并通过 `POST /api/analysis/runs/:runId/retry-ai` 基于同一冻结 snapshot 创建新的
手动报告版本；重试不会再次采集，也不会改变正式 CTA 状态。

## 报告标签

- 正式 · 盘前决策
- 正式 · 收盘复盘
- 手动 · 盘前分析
- 手动 · 盘中分析
- 手动 · 即时分析（休市或盘后）

手动输出描述当前状态、风险、关注标的和解释改变条件；`forecast`、`review`、个股正式预测及
持仓动作字段必须为空，objective evaluation 为 `not_applicable`。
