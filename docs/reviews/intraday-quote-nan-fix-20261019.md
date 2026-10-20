# G1 报价 NaN：本地修复与待部署说明

日期：2026-10-19。用户授权本地修复，并明确收盘后另行部署。本轮没有连接生产环境、部署、重启、修改生产配置／数据库，也没有调用真实行情 provider。

## 问题与修复

开盘只读检查发现，全池报价中的 NaN 导致严格 JSON 序列化失败，G1 外层降级处理将同组有效分钟线引用一并丢弃。ETF 恰好分在 G1，并无证据表明 ETF 本身不能采集。现网观察记录见 [开盘检查](intraday-live-health-20261019.md)。

本地只改两个采集器实现文件：

| 文件 | 最终行为 |
| --- | --- |
| `urus-moomoo/src/urus_moomoo/storage/equity_history.py` | 报价入库前递归将浮点 NaN／正负无穷转换为 JSON null；保留有限值，记录 `nonfinite_fields` 字段路径；不修改传入对象。仍使用严格 JSON、原引用 hash 与同槽幂等校验 |
| `urus-moomoo/src/urus_moomoo/runtime.py` | 排除缺失、非有限、非正值或布尔类型的 last_price，仅该股票报价不可用；报价写入异常局部降级，保留已有效的 1m／5m 引用，报价 coverage 与分片状态标为 partial；不引用旧槽报价 |

可选字段缺值不再使整批报价失败。必要价格无效时不伪造价格、不设为0、不冒充完整覆盖。分钟线 OHLC 的有限值与一致性校验没有放宽。股票 finalizer 仍只做本地引用合并；没有增加 provider 请求、重试、线程、服务、配置项或新数据库迁移。

## 测试证据

- 测试先行：新增报价规范化／无效价格回归先得到 **6 failed、1 passed**；日志 `/private/tmp/urus-quote-nan-red-20261019.log`。失败包括 NaN／±Inf 严格 JSON 异常、G1 bars 引用丢失、0价格未排除。
- 新增分片状态断言先得到 **3 failed**，再同步 partial 状态，避免质量字段与分片展示冲突；日志 `/private/tmp/urus-quote-status-red-20261019.log`。
- 独立 luna-builder 新增 **4 项全链路验收通过**：无效报价、quote存储异常、G2/G3继续执行且本地finalizer不额外请求provider、可选嵌套非有限值的完整槽与backend hydrate。
- 最终 collector 严格离线全量 **168 passed in 5.83s**，联网 guard 无连接尝试。覆盖原157项＋root新增7项＋独立新增4项；日志 `/private/tmp/urus-quote-nan-full-20261019-final.log`。
- 后端引用恢复、规范化数据、页面 projection、事件及结构规则相关 **120 项严格离线通过**、exit0。DNS／IPv4／IPv6 禁止连接；日志 `/private/tmp/urus-quote-backend-target-20261019.log`。
- `git diff --check` 通过。没有前端代码变更。

独立完整报告：[报价 NaN 修复验收](intraday-quote-nan-test-report-20261019.md)。新增测试区分股票 finalizer 与 Eoflora；已有调度／Eoflora优先级测试随全量通过，不声称验证真实 Eoflora provider。

最终实现 SHA256：

```text
runtime.py
5284d69a92e68a4d7678bbb2f8e90c7814aa83c6981c3256546044a69ac47d6f
storage/equity_history.py
89e1f9f96d2f55711418b461951945c8f086c76825dfffbcdff31b278eb5b018
```

## 收盘后应用范围

只需把包含这两个文件的最终 collector 源码构建为新镜像，再按现有调度配置替换 collector。API／前端无需因为本补丁替换，也无须迁移表结构。保留当前旧 collector，维持原六项任务、股票分组、45秒预算及 Eoflora 优先级。不要使用10月17日的旧发布归档代替新补丁。

补丁尚未部署，生产 G1 故障不会因本地测试通过自行恢复。部署后须等待真实新槽验证 G1 bar／quote引用、三组整槽覆盖、报价缺值状态和后续 Eoflora 任务；不能把离线 fake-provider 测试当作生产已恢复。此次不修复历史 capture、不补跑开盘窗口，也不增加历史回补／盘前采集。MACD、RVOL等仍遵循现有历史预热要求。
