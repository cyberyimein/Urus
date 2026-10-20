# P2 股票日内采集：本地验收

日期：2026-10-15。结论：本地代码切片通过；未部署、未启用真实采集。

## 范围与结论

独立 `capture_intraday_equities` 操作提供全池分批报价与准入后的逐股票分钟历史。A/B/C 每槽采集，D 组按排序后的代码分为三个轮换批次；跨组按 A/B/C/D 优先级去重。报价每批最多 400 个代码，分钟请求沿用共享历史额度准入及请求节流，不经过期权链采集。

沿用既有 capture envelope、raw SQLite 和 scheduled slot 账本；未创建新的业务表、发布服务、部署任务或私有配置。runtime 明确区分股票与既有期权操作。

根协调代理审查后提出并修复四项问题：调度槽必须控制交易日和 cutoff；重复不完整槽返回已落盘 contract；分钟时间显式声明 start/end 并输出 UTC 区间；非法或缺失的价格与量额不能默认为零或标记 ready。再次审查相关分支及存储读回逻辑，未发现阻塞本地切片验收的问题。

## 独立测试证据

由指定的 `luna-builder` 测试子 agent 执行，先记录红灯，再复验修复：

| 检查 | 结果 |
| --- | --- |
| 新增股票日内验收 | 18 passed |
| 采集器 quota/pacing、runtime/scheduler、配置、preflight/publisher 回归 | 52 passed |
| 后端历史额度与既有期权日内回归 | 23 passed |
| 相关 Python 编译与 diff 空白检查 | 通过 |

测试清单、命令和红绿灯记录见 [独立测试报告](intraday-equities-test-report-20261015.md)。全部使用 fake SDK 和临时 SQLite。

## 实际启用前仍未验证的事项

- Provider 的分钟时间字段边界尚未通过真实样本确认。代码默认 start 是明确记录的假设，输出 `provider_boundary_verified=false`；未来启用前必须确认并选定正确口径，不能靠自动猜测。
- Fake SDK 证明编排与保护行为，不能证明账户当前额度、权限或生产耗时。没有因本轮测试请求真实 OpenD。
- 此切片尚无独立日内 UI、正式策略事件、通知或 CTA 模型。UI 属 P3，事件与结构策略按后续阶段实施。
- 未执行部署脚本、SSH、迁移、容器操作或真实采集。交易时间不动部署环境的约束继续有效。
