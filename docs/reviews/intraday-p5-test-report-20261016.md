# Urus P5 结构事件 UI 验收记录

日期：2026-10-16\
范围：P5 S4–S8 事件展示的本地隔离验收\
执行者：acceptance-tests

## 结论

P5 UI 隔离验收 **通过**。Playwright 脚本共覆盖 5 个策略筛选及 4 个关键展示契约，所有硬断言通过；P4/P5 后端联合验收 **64/64 通过**（P4 49 项、P5 15 项）：

- S4–S8 策略筛选均能返回事件，并显示对应中文策略与 setup 标签；
- S8 显示“当日简化九转”和“衰竭观察”，明确写出“**不代表反转确认**”，展开证据可见 `count: 9` 与 `sequence_started_at`；
- S7 展开“依据与冻结线”后可见 `parent_setup_id`、`parent_strategy` 及父 S5 setup 身份；
- S5 的 candidate→invalidated 历史行均显示当前状态“已失效”，过滤到 S5 时不再保留冻结线；清除筛选后，仍处于 candidate/confirmed 的 S4/S6 恢复确认线和失效线；
- 没有未匹配的 API、非 GET API 请求、浏览器 console error 或 page error。

开发 WIP 曾在 `backend/app/intraday/events.py:130` 使用尚未初始化的 `freshness_reason`，因此先前新增 5 项只记录为阻塞；开发声明稳定后已重新执行当前 49+15 项，全部通过。后端测试使用项目本地测试库/临时 SQLite，不连接 provider 或生产 sidecar。

## 命令与隔离方式

前端构建（开发冻结快照）已通过：

```text
npm --prefix frontend run build
```

本轮构建输出为 `vue-tsc --noEmit` 通过、Vite 81 modules transformed、production bundle 构建成功。

后端联合命令及结果：

```text
backend/.venv/bin/pytest backend/tests/test_intraday_events_acceptance.py backend/tests/test_intraday_structure_acceptance.py -q
................................................................         [100%]
```

收集数为 `test_intraday_events_acceptance.py: 49` 与 `test_intraday_structure_acceptance.py: 15`，合计 **64/64 passed**；仅有既存 Starlette/httpx deprecation warning。

本轮 Playwright 使用固定 fixture，仅启动本次测试拥有的 loopback Vite 进程：

```text
/private/tmp/urus-intraday-p4-review-20261016/run_playwright_p5.sh
```

runner 通过 `/Users/waynewong/.codex/skills/playwright/scripts/playwright_cli.sh` 执行，端口为 `127.0.0.1:5187`。所有 `/api/*` 请求由 `p5_fixture.mjs` 在 Playwright route 中返回；脚本拒绝非 loopback、未知 API 和非 GET 请求。脚本退出时清理了本次 Vite PID，复核 `lsof -nP -iTCP:5187 -sTCP:LISTEN` 无残留监听。

测试代码与 fixture 位于：

- `/private/tmp/urus-intraday-p4-review-20261016/p5_fixture.mjs`
- `/private/tmp/urus-intraday-p4-review-20261016/p5_playwright_code.js`
- `/private/tmp/urus-intraday-p4-review-20261016/run_playwright_p5.sh`

fixture 只用于验证页面契约，不代表真实 provider、OpenD、sidecar 或生产数据行为。

## 通过证据

Playwright 输出为 `status: passed`，本轮再次执行包含 S4、S5、S6、S7、S8 五个策略筛选，API 请求数由脚本记录并完成完整性检查。截图：

- [P5 事件面板局部](../../output/playwright/urus-intraday-review-20261016-p5/p5-event-panel.png)
- [P5 事件面板全页](../../output/playwright/urus-intraday-review-20261016-p5/p5-event-panel-full.png)

局部截图可直接看到 S8 九转说明、S7 父 setup 对应的事件行、S6/S4 活动事件和 S5 candidate/invalidated 终态；硬断言另行展开了 S7/S8 的 evidence JSON，并验证 S5 终态冻结线清除及 S4/S6 线恢复。

## 未覆盖项

- 本轮不验证真实 provider、OpenD、生产 SQLite、容器、部署、网络通知或事件消费者实际运行；
- 前端父事件信息目前通过展开 evidence JSON 展示，未新增独立的父事件卡片或专用字段布局。

P4 的长期后端与 UI 记录见 [intraday-p4-test-report-20261016.md](intraday-p4-test-report-20261016.md)。
