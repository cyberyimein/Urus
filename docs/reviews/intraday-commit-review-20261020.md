# 日内观察相关改动：代码审查与提交整理

2026-10-20。用户确认审查全部相关未提交功能，并拆成逻辑清晰的本地 commit。本轮不推送、不部署、不运行真实行情采集；本机 `.codex/config.toml`、环境文件、数据库、镜像、构建产物与浏览器运行产物不纳入提交。

## 审查发现与处理

发现并修复 **1 个 P2：share-class ticker 与普通 ticker 的身份碰撞**。原 quota normalizer 对所有带点代码取最后一段，使 `BRK.A`、`US.BRK.A` 和股票 `A` 都变成 `A`。额度明细、计划、scope 和请求去重因此可能错误复用名额或丢弃另一个股票。原报价适配器还会把裸 `BRK.A` 当成已带市场前缀，且卡片/快照显示也截成 `A`。

修复使用两个独立运行包各自的纯 symbols 模块，在包内让额度与报价共用归一化规则：只移除已知市场前缀，保留 ticker 内部的点；裸 `BRK.A` 转为 `US.BRK.A` 请求，显示仍为 `BRK.A`。没有引入 API/collector 之间的运行时依赖，没有修改旧数据库。

关键代码：

- `backend/app/core/symbols.py`、`backend/app/services/history_quota.py`、`backend/app/integrations/moomoo.py`。
- `urus-moomoo/src/urus_moomoo/symbols.py`、`services/quota.py`、`integrations/moomoo.py`。

采用先红后绿：backend identity 测试先 3 失败、market card 测试先 1 失败，collector identity/admission 先 4 失败。修复后相关 backend 30 项、collector 37 项通过。回归断言覆盖 `BRK.A`/`BRK.B`、普通 ticker、既有 US/HK/SH 前缀、请求代码、显示、scope、明细、批量去重，以及剩余额度为 0 时 `A` 可以复用但 `BRK.A` 不得借用其身份。

其余审查未发现可复现的新增 P1/P2 阻塞缺陷。审查覆盖日线 MACD/版本身份/冻结输入、观察页读取优化、采集器 quota/pacing/共享锁、Eof 优先级、分组调度与截止、历史缓存/refs/保留清理、跨日预热、事件状态机/消费者、CTA 代理、replay/outcomes，以及前端请求取消、整点刷新、图表时间轴和标的池设置。静态审查与 fake-provider 回归不等于真实策略效果或线上额度保证。

## 提交分组

| 顺序 | 提交 | 内容 | 独立验证 |
| --- | --- | --- | --- |
| 1 | `a286877` | MACD H/ΔH/Δ²H、日线展示、冻结版本、观察页/读取性能与相关测试 | 临时源码树 backend 344 项；前端构建 75 modules |
| 2 | `3d8371f` | 日内采集/规范化存储/refs、Eof 优先与分组时窗、事件/CTA/replay、页面与池设置 | 临时源码树 backend 578 项、collector 183 项；前端构建 88 modules |
| 3 | `de0d645` | 个股盘前历史缓存检查与有界补齐、类型作用域、预热测试和部署记录 | 修复前基线 backend 579 项、collector 194 项；独立预热 3 项 |
| 4 | `fix: preserve share-class symbols in quota and quote identities` | 本次审查发现的身份冲突修复及回归 | 最终 backend 587 项、collector 202 项；网络隔离日志无外连 |

前三笔根据测试过的精确源码内容写入 Git 索引。没有回退工作区、丢弃本地修改或依靠未提交文件补齐依赖。第二笔使用预热之前的 6 个源文件版本及对应基础测试；第三笔只引入预热增量；第四笔独立保留本轮审查修复。新文件的 EOF 空白已清理，Markdown 的两空格硬换行改为反斜杠硬换行以保留显示语义。

## 验证与证据

- [Luna 独立测试报告](intraday-commit-test-report-20261020.md)：最终完整 backend/collector 回归、前端构建与双图 Playwright。
- 拆分源码、文件清单、各笔内容 SHA256 与日志：`/private/tmp/urus-commit-review-20261020/`。
- 两笔基础提交分别在 `git archive HEAD` 生成的临时源码树中验证，依赖原虚拟环境与 node_modules，不读取部署目录或生产数据库。
- 使用带 `__main__` 保护的网络 guard 拦截外连；没有新增前端 UI 单元测试。前端未因身份修复变更，不重复运行图表测试。
- 提交候选文件进行了私钥、常见访问 token/密钥模式检查；未发现匹配。该检查不是全面安全审计。
- `git diff --cached --check` 用于每笔提交，最终再次核对工作区与提交内容。无远端 push。

## 已知限制

原日内事件健康记录中的 `attempt to write a readonly database` 历史错误未在本轮修复或清除；不能用本地测试或 HTTP 200 将其宣称为已恢复。真实 quota、历史缺口和策略效果仍按运行时状态处理。本次身份修复只进入本地代码与 commit，没有再次部署，线上运行版本保持既有预热发布。
