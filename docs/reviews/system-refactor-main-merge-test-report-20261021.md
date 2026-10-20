# system-refactor 分支合并前本地验收（2026-10-21）

本报告记录分支合并到本地 `main` 前的独立回归结果。本轮只运行本地测试和前端构建，
没有执行 `git merge`、`git commit`、SSH、生产 API、provider、部署或容器操作。

## 验收对象

当前分支为 `codex/system-refactor-2`，HEAD 为 `ca51589`；本地 `main` 为 `ed7ff7d`。
`main..HEAD` 共 8 个提交，顺序如下：

```text
8e61426 feat: add market change discovery workflow
720a124 fix: isolate unavailable discovery indicators
8594a93 feat: filter discovery events by indicator
d62c62d fix: style discovery indicator menu
a286877 feat: add MACD momentum and streamline observation views
3d8371f feat: add intraday observation and budgeted collection
de0d645 feat: prewarm stock MACD from cached session history
ca51589 fix: preserve share-class symbols in quota and quote identities
```

本次定向覆盖用户指定的早期 discovery/focus/observation/migration 功能：

```text
backend/tests/test_focus_symbols.py   3
backend/tests/test_observation.py    47
backend/tests/test_migrations.py      3
```

合计 **53 项**。

## Backend 定向回归

从 `backend/` 目录使用项目 Python 3.12 虚拟环境运行：

```text
cd /Users/waynewong/code/Urus/backend
PYTHONPATH=/private/tmp/urus-predeploy-20261016/siteguard:/Users/waynewong/code/Urus/backend \
APP_ENV=test \
DATABASE_URL=sqlite:////private/tmp/urus-system-refactor-merge-20261021/backend-fallback.sqlite \
SIDECAR_CAPTURE_DATABASE_PATH=/private/tmp/urus-system-refactor-merge-20261021/backend-sidecar.sqlite \
MOOMOO_ENABLED=false FRED_ENABLED=false YAHOO_ENABLED=false \
ANOMALO_WORKFLOW_ENABLED=false OBSERVATION_UNIVERSE_SOURCE_URL= \
INTRADAY_EQUITIES_ENABLED=false INTRADAY_EVENTS_ENABLED=false SYSTEMATIC_RISK_ENABLED=false \
NETWORK_GUARD_MARKER=/private/tmp/urus-system-refactor-merge-20261021/network.log \
.venv/bin/pytest -q tests/test_focus_symbols.py tests/test_observation.py tests/test_migrations.py
```

结果：**53 passed**，退出码 0。

- `test_focus_symbols.py` 验证关注标的规范化、幂等保存/删除、非法输入和不改变
  observation/universe/run 作用域。
- `test_observation.py` 验证 group snapshot、breadth/rotation/heatmap、质量降级、
  决策去重与成员归属等观察页行为。
- `test_migrations.py` 验证空库迁移、从旧 ORM 状态继续迁移，以及旧 instrument bars
  回填 canonical daily bars。

首次从仓库根目录以同一组路径运行时，focus/observation 的 50 项通过，但 3 个迁移用例
因 Alembic 配置中的相对 `script_location=alembic` 无法解析而失败（`Path doesn't exist:
alembic`）。从 `backend/` 按项目实际运行方式重跑后 3 项全部通过；这不是业务失败。

日志：

- 首次目录错误记录：`/private/tmp/urus-system-refactor-merge-20261021/logs/backend-targeted.log`
- 正确目录最终结果：`/private/tmp/urus-system-refactor-merge-20261021/logs/backend-targeted-correct-cwd.log`

network guard 日志为空：
`/private/tmp/urus-system-refactor-merge-20261021/network.log`。

## Frontend build

运行：

```text
npm --prefix frontend run build
```

结果通过：`vue-tsc --noEmit` 成功，Vite 完成 **88 modules** 构建，产物为：

```text
frontend/dist/assets/index-CQFXFf8n.js
frontend/dist/assets/index-DWN1bHP7.css
```

本轮没有新增 frontend 单元测试，也没有使用 Computer Use 或浏览器点击验收；前端测试
仍遵循项目约定由 Playwright 专项负责。

构建日志：`/private/tmp/urus-system-refactor-merge-20261021/logs/frontend-build.log`。

## 工作树和限制

测试前 `git status` 仅显示既有未跟踪的 `.codex/config.toml`；本轮没有修改业务代码、
已有测试或提交。`git diff --check` 通过。

本报告只证明当前分支在隔离本地 SQLite 和禁用 provider 的条件下通过指定回归。它没有
验证合并冲突、合并后的完整系统套件、真实数据源、生产迁移或部署行为；这些应在 root
完成本地 merge 后再按其最终工作树复核。
