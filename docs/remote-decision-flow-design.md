# Remote Decision Flow 设计

本文定义 Urus 当前的远程 AI 决策接缝。它只描述用户确认后的远程执行，不负责市场数据采集、期权采集或本地事实生成。

## 1. 责任划分

```text
Urus                                             Anomalo
────────────────────────────────────────────     ─────────────────────────
Universe / 日线 / 期权 / 策略事实                 已发布 Workflow 执行平面
冻结 source locator                              节点编排、模型调用和远端事件
输入编译、hash、scope 校验                        运行实际 Workflow
队列、重启恢复、artifact 验收                      返回状态、结果和 trace reference
```

Urus 是事实和验收边界，Anomalo 是执行边界。远程返回不能覆盖 Dataset、Chart Projection、Strategy Decision 或 Option Evidence。

## 2. 入口与来源

支持四种 `intent_type`：

- `instrument_arbitration`：一个标的的策略结果仲裁；
- `group_arbitration`：一个观察组快照的结构化仲裁；
- `indicator_attention`：一次观察运行中的指标异常交叉分析；
- `strategy_attention`：一次观察运行中的策略状态交叉分析。

每个入口都接收 immutable source locator。locator 只能指向已有的 Dataset、Chart Projection、Group Snapshot 或 Observation Run，不允许 `latest`、`current`、自由 symbols、前端传入的 evidence 或未知字段。

## 3. Preflight 和冻结输入

`RemoteDecisionCompiler` 在提交前完成：

1. 校验 intent 与 active Workflow Binding 的对应关系；
2. 读取 source locator 指向的不可变证据；
3. 校验 scope、组版本、交易日和 Dataset/快照身份；
4. 编译确定性投影，限制交叉标的行数、notable cards 和总字节数；
5. 生成 `input_sha256` 与 `preflight_fingerprint`；
6. 汇总 blocker/warning，只有无 blocker 且 binding 可用时才允许提交。

预检不会启动远端运行。页面刷新不会重复预检以外的副作用。

当前输入和结果版本：

- input：`urus.remote_decision_input.v1`；
- artifact：`urus.remote_decision_artifact.v1`；
- 输入必须包含精确的 source、scope、evidence summary、quality、strategy/option facts 和 `input_sha256`。

## 4. Workflow Binding

Binding 是运行前保存的不可变发布凭据，至少包含：

- `intent_type`；
- 精确 `workflow_ref`（name + version）；
- definition hash；
- compiled hash；
- capability manifest hash；
- input/output Schema version；
- active/inactive 状态。

运行时只读取 active binding，且服务端必须再次比较意图、Workflow Ref、hash 和 Schema。运行配置不从页面参数动态拼装，也不持有发布期管理凭据。

## 5. 提交、跟踪和恢复

`POST /api/remote-decisions/preflight` 只做预检；`POST /api/remote-decisions` 创建本地记录并返回 queued 状态。`RemoteDecisionSupervisor` 使用有界队列：

1. 记录 local run、输入 hash 和幂等键；
2. 通过 Anomalo adapter 启动精确 Workflow；
3. 在收到远端 run id 后立即持久化，支持用户停止；
4. 保存递增事件序号，服务重启后从数据库继续读取；
5. 受控轮询直至成功、失败、停止或超时；
6. 将远端结果交给 artifact 验收器。

同一 `request_intent_id + source identity + input hash + binding hash` 不能产生重复提交。显式 rerun 生成新的本地运行和幂等键，不覆盖旧记录。

## 6. Artifact 验收

`validate_artifact()` 必须确认：

- schema version 和 intent 正确；
- `input_sha256` 与本地冻结输入一致；
- scope type、scope id、组版本、Dataset/快照身份一致；
- completeness、decision、evidence refs 和 notable cards 符合边界；
- 不存在未声明字段、越过输入范围的 symbol 或过量内容；
- 若 artifact 声称使用期权或策略事实，引用必须能回到冻结 payload。

验收失败的结果只保存安全错误、远端状态和 trace reference，不保存为可展示决策。远端运行失败也不删除本地证据。

## 7. 前端行为

- 个股、观察组和交叉分析页面共享 `RemoteDecisionPanel`，按钮只在 preflight 通过后可提交；
- 运行详情页展示 queued、submitting、running、stopping、succeeded、failed、stopped 和 rejected result；
- 页面显示 Workflow Ref、输入 hash、质量警告和安全错误，不显示 token；
- 用户可主动查看 provider 返回的 trace reference，但不能把 provider reasoning 当作验证后的证据；
- 没有已冻结证据、binding、官方 close 或必要期权结构时，UI 明确显示 blocker，不生成模拟结论。

## 8. 失败策略

| 场景 | 处理 |
| --- | --- |
| binding 缺失或 hash 不匹配 | preflight blocker，不提交 |
| source locator 不可验证 | preflight blocker，不读取自由范围数据 |
| 输入超限 | preflight blocker，要求缩小确定性投影 |
| Anomalo 认证/网络失败 | 保留安全错误，可按策略重试 |
| 远端返回错误运行类型或 Workflow Ref | 拒绝结果 |
| 远端成功但 artifact 缺失/越界 | `rejected_result`，不展示为决策 |
| Urus 重启 | supervisor 从本地状态和事件序号恢复 |
| 用户停止 | 向远端发送 stop，最终状态以远端确认和本地状态为准 |

## 9. 实现索引

| 边界 | 实现 |
| --- | --- |
| 输入编译、hash、artifact 验收 | `backend/app/decision_harness/remote_workflow.py` |
| HTTP/Fake adapter | `backend/app/integrations/anomalo_workflow.py` |
| 本地队列和生命周期 | `backend/app/services/remote_decision_supervisor.py` |
| 持久化模型和仓储 | `backend/app/models/remote_decision.py`、`backend/app/repositories/remote_decision.py` |
| API | `backend/app/api/remote_decisions.py` |
| 页面 | `frontend/src/components/decision/RemoteDecisionPanel.vue`、`frontend/src/views/RemoteDecisionRunView.vue` |
| 发布配置审计 | `backend/app/decision_harness/workflow_definitions/`、`backend/workflow-bindings.json` |

远程 AI 的新增能力必须先扩展 source locator、输入/结果 Schema 和验收测试，再扩展 adapter 或页面；不得通过绕过 compiler 的快捷接口提交。
