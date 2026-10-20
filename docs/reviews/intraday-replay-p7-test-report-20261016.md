# P7 离线回放与结果测量红灯记录（2026-10-16）

本轮只新增 `backend/tests/test_intraday_replay_acceptance.py`，未修改业务代码、实时存储、生产配置或已有测试。测试夹具在内存中生成带 canonical hash 的 P2 equities capture 和显式 UTC 5 分钟 bars；CLI 用例只写 pytest 临时目录。

## 初始红灯与当前定向复验

```text
backend/.venv/bin/pytest backend/tests/test_intraday_replay_acceptance.py -q
15 failed
```

初始红灯来自接口缺失：`app.intraday.replay` 与 `app.intraday.outcomes` 尚未存在，离线 CLI `python -m app.intraday.replay` 也因此无法启动。没有把 ImportError 当成业务行为通过。

开发接入模块后，补充了真实 P3 聚合到共享 P4 evaluator 的长观察 fixture，并按新的 future/known-after 时间语义修正了边界夹具。此前基础定向复验为 **19/19 通过**：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_replay_acceptance.py -q
...................                                                      [100%]
```

根终审要求的三项边界已追加，当前定向复验为 **20/22 通过、2 项红灯**：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_replay_acceptance.py -q
...F............F.....                                                   [100%]
20 passed, 2 failed
```

两个红灯均对应业务实现待修边界：同一 task 在 reconstructed 与
original_observed 之间切换时应跳过第二类 provenance，并返回
`mixed_provenance_for_task`；内部连续但相对交易所开盘网格错位两分钟的 5 分钟
bars 应标记 outcome 不可用。正常日与半日收盘前不足 15 分钟的用例、半日有完整
同 session 窗口的用例已通过。

上一轮两项实现已修复后，根审计又要求保留两类可解释性证据，测试已追加但业务
尚待落地。追加审计用例的首轮复验为 **22/24 通过、2 项红灯**：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_replay_acceptance.py -q
............FF..........                                                 [100%]
22 passed, 2 failed
```

红灯分别要求：边界未验证时保留零正式事件的 evaluation 摘要及
`minute_timestamp_boundary_unverified` reason count；空输入也必须带稳定的规则快照
hash、projection 参数和空 evaluations，修改 cooldown 等 spec 后 hash 必须变化。

开发补齐报告身份与紧凑 evaluation 摘要后，最终 P7 定向复验为 **24/24 通过**：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_replay_acceptance.py -q
........................                                                 [100%]
24 passed
```

其中 slot 9（75 分钟、15 根 5m）产生真实 S4 ORB candidate，slot 10（80 分钟、16 根 5m）沿用前一状态确认同一 setup；测试同时确认 task 隔离、同 task 晚修订不重复、未来观察不重绘既有 event prefix。future slot（scheduled time 晚于 observed time）与 known-after-observation（captured time 晚于 observed time）使用不同 capture 形状并分别检查跳过语义。

## 覆盖的最小契约

- 回放按 `observed_at` 排序，按 `task_name` 隔离状态；重复槽和迟到旧槽不覆盖单调 cursor/state。
- capture hash、aware UTC 观察时间、未来槽、观察时尚未可知的 capture、非法 provenance 均显式跳过；`original_observed` 与 `reconstructed` 分组计数。
- 单 task provenance 边界、不同 task 混合 provenance，以及分开回放各 provenance 的隔离行为。
- 结果至少暴露 `schema_version`、`observations_processed`、`skipped`、`events`、`states_by_task`、`counts_by_provenance`。
- 回放报告保留规则快照 hash、projection 参数及紧凑 evaluation 身份／能力摘要；零事件和空输入也可解释。
- outcome 只允许 15/30/60 分钟；锚点使用 cutoff 前完成 5 分钟 bar 的 close，同交易日、连续、完整且合法的 5 分钟窗口缺一即不可用。
- 5 分钟 bar 必须落在 XNYS session-open 网格；正常日与半日收盘前不足固定窗口时不可跨收盘或跨 session 补齐，半日中有足够同 session 数据仍可用。
- outcome 独立计算 forward return、非负最大上行、非正最大回撤；bullish/bearish 才解释冻结确认／失效线，RS 事件返回 `not_applicable`；同一 bar 同时触线返回 `same_bar_unknown`。
- CLI 读取 JSON observations、生成新报告并拒绝覆盖已有输出；不导入 Settings，不访问 sidecar／主库/provider。

## 后续

本轮冻结后的本地联合证据如下，均使用 `/private/tmp` 网络 guard、临时 SQLite
路径和关闭 provider 的测试环境：

```text
P4–P7 联合（events49 + structure15 + replay24 + systematic-risk24 + service26）: 138/138 passed
backend 全量（审计测试追加前）: 518/518 passed
urus-moomoo/tests 全量: 70/70 passed
npm --prefix frontend run build: passed (vue-tsc + vite, 84 modules)
```

这些是固定离线 fixture 和本地实现的验收证据，不代表真实历史覆盖、账户容量、
provider 时间边界或生产 shadow 已完成；本轮未部署、未连接 OpenD、未读写生产数据库。
