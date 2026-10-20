# 采集器部署与收盘恢复记录（2026-10-04）

## 结果

用户明确授权部署及使用恢复命令。新采集器于 2026-10-04 10:25:15 UTC（日本时间 19:25:15）启动，18 个交易日的最终分钟线和 4,116 条收益标签已恢复。生产 API 保持运行。本次仅部署独立采集器；工作区的 backend/frontend 性能补丁没有包含在该镜像中。

## 部署身份

- 新镜像：`urus-moomoo:scheduler-20261004-nMm7M9`。
- 新容器：`urus-moomoo-scheduler-20261004-nMm7M9`，4 CPU、2 GiB，沿用 `urus-internal` 网络、已有数据卷和私有 task 配置。
- 镜像 manifest list digest：`sha256:aac6c2fcfcc1461bdaa89c2d09940084cff9802c46652ccd042ad30045a7d6d5`。
- 源码包 SHA-256：`2a4cdd989fd24b238be97b957ef89d8250d10601859d5b880409d7bc67b77984`；23 个 Python 文件在新镜像中逐项核对通过。
- 基于原生产镜像复用依赖，仅增加当前 sidecar 源码层；未重新安装依赖或改变私有配置。
- 旧容器 `urus-moomoo` 已停止并保留，旧镜像 `urus-moomoo:20260911-dns-plan-get-fix-1` 保留。没有删除容器或覆盖配置。

原部署脚本包含同名容器删除和私有配置重写，本次采用新名称切换以保留回退点。后续维护应以新容器为实际采集器；使用原部署脚本时须明确设置 `CONTAINER_NAME=urus-moomoo-scheduler-20261004-nMm7M9`，避免按旧名称另起第二个采集器。

## 备份和恢复范围

生产主机备份文件：

```text
/Users/mithridates/data/urus/deploy/scheduler-20261004-nMm7M9/intraday-before-recovery.db
```

使用 SQLite backup API 从只读源创建一致性备份，文件为新建且不覆盖已有文件，大小 602,419,200 字节，`quick_check=ok`。备份前 outcomes 为 0 行。

新镜像首先挂载只读生产数据，对全部 18 个日期执行 `recover_close(..., apply=False)`：kind、日期及内容 hash 均通过，每天 3 个标的、1,170 根最终分钟线。之后在新采集容器中执行同一恢复函数的 `apply=True` 路径，直接使用存量原始包，没有重新请求行情。

恢复日期：2026-09-09、10、11、14、15、16、17、18、21、22、23、24、25、28、29、30、2026-10-01、02。

- 9 月 9 日：63 个成功盘中 run，恢复 189 条 outcomes。
- 其余 17 日：各 77 个成功 run、231 条 outcomes。
- 合计：18 日、4,116 条 outcomes。

9 月 8 日有 28 个成功盘中 run，但没有对应的已存收盘包，未生成该日收益标签。9 月 17、18、21 日具备盘中收盘 finalizer 包，本次已恢复盘中结果；此前缺失的市场/期权收盘观察报告是不同链路，仍没有对应原始包，不以本次恢复结果宣称已补齐。

生产主机同目录保存 `source-manifest.json` 和仅含聚合结果的 `recovery-results.json`。本仓库记录不包含私有配置、原始行情或原始日志。

## 上线验收

截至 2026-10-04 10:27:24 UTC：

| 检查 | 结果 |
| --- | --- |
| 新容器 | running，镜像身份符合部署版本 |
| 旧容器 | stopped，无第二个采集器同时运行 |
| 新容器到 API | health HTTP 200，production、database=ok |
| 最新观察报告 | 2026-10-02 succeeded，原 run 身份未改变 |
| SQLite | quick_check=ok，foreign_key_check=0 |
| 收益标签 | 4,116 行，重复 run/symbol=0 |
| 恢复区间的成功采样 | 缺少 outcomes 的 eligible selected spot=0 |
| 最新分钟线 | SPY、QQQ、IWM 均到 2026-10-02 20:00 UTC，即美东 16:00 |
| 最新一天分钟线数量 | 每标的 390 根，供应商分钟结束时间标记 09:31–16:00 美东 |
| 近期手动 audit | 9 月 22 日至 10 月 2 日共 9 个交易日，market/options/close 完成日期均无缺口 |
| 启动日志聚合 | 配置加载成功；Traceback、tick failure、publication failure 均为 0 |

部署时为周日，没有人为触发新行情采集；因此上线检查验证了运行、连通、版本及历史恢复，不将其表述为新版本已完成下一交易日的自动采集验收。

## 回退

初次部署时保留旧容器；随后用户明确要求不保留旧容器，`urus-moomoo` 与预览容器已在性能版本上线验收后删除。当前状态见 [性能部署验收](performance-optimization-deployment-20261004.md)。如需回退，应以旧镜像重新创建容器，并先停止当前采集器，避免两个采集器同时运行。此次恢复使用既有表结构，采集器回退无需自动回滚数据库。数据库备份保留；如需覆盖数据库恢复备份，必须另行明确授权该覆盖操作。
