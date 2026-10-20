# 性能补丁部署验收（2026-10-04）

## 部署结果

后端性能补丁与前端构建已于 2026-10-04 10:52:50 UTC（日本时间 19:52:50）上线。用户同时明确要求不保留旧容器，旧 API、旧采集器与部署预览容器已在上线验收后删除。生产数据及数据库备份保留。

当前 Urus 运行容器：

- API / UI：`urus-performance-20261004-fNewAR`，镜像 `urus:performance-20261004-fNewAR`，8 CPU / 6 GiB，宿主端口 7777 → 8000。
- 采集器：`urus-moomoo-scheduler-20261004-nMm7M9`，镜像 `urus-moomoo:scheduler-20261004-nMm7M9`。

## 构建范围和身份

性能镜像以原生产 API 镜像为基础，覆盖 11 个已修改的 backend/app Python 文件，并加入当前工作区的完整前端生产构建。前端包括已有的 CrossSection 可视化布局改动；这些布局改动不作为本轮性能收益的证据。没有把本地数据库、测试文件或私有配置放入镜像。

原生产 `pyproject.toml`、`uv.lock`、API supervisor 与本地文件 SHA-256 一致；抽查的原 Repository、Discovery、CrossSection 源码与补丁的 HEAD 基线一致。无需升级依赖或增加 migration。新容器沿用原有效环境、数据卷、备份卷、网络与端口；环境值未输出至日志或仓库。

- 部署源码包 SHA-256：`3ec540c3c9b78407fea6d06a96c28b4691d6722810d66e4c3bf4d4eec5d8e583`。
- 镜像 manifest list digest：`sha256:9ee92d638a588e805d987973d142427d26abf4a7860d27fdded66ee8d784ce63`。
- 镜像内 `performance-source-manifest.json` 中 15 个后端/前端产物逐项核验通过。

源码包、manifest 和备份保存在生产主机独立部署目录 `deploy/performance-20261004-fNewAR`。

## 部署前验证

- backend 全套：315 tests，通过。
- frontend：88 tests，通过；TypeScript 检查与 Vite 生产构建通过。
- sidecar：52 tests，通过。
- `git diff --check` 通过。

曾将 backend 和 sidecar 合并到同一 pytest 进程执行，出现一条 sidecar 日志捕获断言未捕获日志；随后将两套独立服务的测试分别放入独立进程，全套均通过。该记录保留，不把合并执行表述为通过。

## 备份

切换前使用 SQLite backup API 新建主业务库一致性备份：

```text
/Users/mithridates/data/urus/deploy/performance-20261004-fNewAR/urus-before-performance.db
```

大小 1,912,578,048 字节，quick_check=ok，备份中 Observation Run 为 31 条，Alembic revision 为 `0026_focus_symbols`。此前盘中恢复前备份也继续保留，位置见 [采集部署记录](scheduler-collection-deployment-20261004.md)。没有使用本地历史数据库替换生产库。

## 在线验收

同一份生产冻结数据在切换前后执行只读验证：

| 验收项 | 结果 |
| --- | --- |
| API health | HTTP 200，production、database=ok |
| 8 条 Run 列表 | 旧完整列表 5,854,402 字节；新 summaries 3,329 字节 |
| Run 身份及状态 | 新摘要与旧列表中的 run/date/status/universe/count 元数据一致 |
| Discovery | HTTP 200，59,651 字节；规范化完整 JSON SHA-256 与旧版本相同 |
| SPY 单标的证据 | HTTP 200，1,632,536 字节；旧完整 Chart 16,327,442 字节 |
| SPY 图表数据 | instruments 仅包含 SPY；所选 instrument 的规范化 JSON SHA-256 与旧完整图表中的 SPY 相同 |
| 机会视图 worker | 对已有 Dataset 的 SPY 请求 HTTP 200，返回既有 response schema，单次约 1.56 秒 |
| 前端资源 | 页面实际引用新 JS/CSS，HTTP 返回内容的 SHA-256 与构建 manifest 一致 |
| 数据库 | Run 仍为 31 条，revision 仍为 `0026_focus_symbols` |
| 采集器到 API | 原 gateway 地址访问 health 与新 summaries 均 HTTP 200 |
| 已恢复 outcomes | 仍为 4,116 条 |
| 新服务日志聚合 | Traceback、500、scheduler tick failure 均为 0 |

新 Run summaries 的单次请求约 23.86 ms，旧完整列表单次约 176.07 ms；这两次请求返回的内容范围不同，只用作上线连通与响应规模证据，不是整页/P95 性能基准。单标的 evidence 与旧完整 Chart 的外层契约也不同；已验证所选标的数据一致，不宣称完整响应字节相同。

## 清理结果

上线验收后按用户授权删除以下已停止容器：

- `urus-v3-cards-ui-20260925`
- `urus-moomoo`
- `urus-moomoo-preview-20261004-nMm7M9`

新 API 与新采集器持续运行，持久化数据卷和备份保留。后续运维应使用上述新容器名称，不再启动旧名称的另一份采集器。原镜像仍存在于本机镜像存储中；旧容器已不存在，不能再直接用 `container start` 恢复旧容器。

部署发生在周日，本次没有触发新市场采集或创建 Observation Run。下一交易日的自动采集行为仍须以实际运行记录验收。
