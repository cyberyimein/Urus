# MACD 双图版本：开盘前部署

2026-10-20。用户明确授权“还没开盘可以部署”。切换前纽约时间为 05:09 EDT，未进入 09:25–16:05 保护窗口。

## 已部署

- API 容器：`urus-api-macd-overlay-20261020`。
- 镜像：`urus:intraday-macd-overlay-20261020`。
- Image digest：`02a84cc344d095aa1cba40e2b4b6395d78d5e9837bfd8e7da763f94bdfd4f7fa`。
- 启动时间：2026-10-20 09:09:44 UTC，即 **18:09:44 JST**。
- 沿用原环境、8 CPU/6 GiB、urus-internal/MTU1280、7777→8000、原数据与备份挂载。
- 旧 API `urus-api-intraday-20261020` 停止保留，可在新 API 停止后恢复；未删除容器，未覆盖数据库或新增 Urus 主库备份。

相对上一发布源码仅更改两个文件：`frontend/src/views/IntradayObservationView.vue` 和 `frontend/src/styles/intraday.css`。新冻结上下文 `artifacts/intraday-macd-overlay-20261020/` 共 258 个 manifest 文件；后端、collector 和迁移源码保持上一发布身份。运行 API 内 169 个后端/迁移/启动源码 SHA256 逐项匹配。

## 页面内容

H 为上图柱状图；ΔH 与 Δ²H 在下图以蓝、金双线叠加；原始数值同显。三项过零信号按圆/三角/菱形标记在对应 H 柱体上，同柱多信号完整提示合并。两图与 K 线共用时间轴，缺口不跨线，缺失 H 时不虚构信号柱。

## 采集保护

本次没有重启或替换采集器及 Eoflora 应用：

| 服务 | 状态 | 启动时间 UTC（部署前后相同） |
| --- | --- | --- |
| urus-moomoo-intraday-20261020 | running | 2026-10-20 00:17:11 |
| eoflora-current | running | 2026-10-18 18:52:32 |

切换前后日内池响应相等；Universe revision=12，revision/content_sha256 一致；日内池 revision=0。未执行手动采集、provider 请求、配置 PUT/POST 或生产数据库清理。前次 collector 一致性备份继续保留。本轮无新 Alembic 迁移或 collector 表变化。

## 验收

- 新鲜前端 vue-tsc/Vite 构建通过，88 modules；双图本地 Playwright 已通过 25 个固定 fixture GET，覆盖过零柱体定位、两线、缺口、空值及窄屏。
- 原 backend 578/collector 183 全套回归身份仍适用，本次没有后端/collector 代码变化，未重复运行无关完整测试。
- Luna 部署后独立只读验收 21 个 GET 通过，详见 `intraday-postdeploy-macd-overlay-test-report-20261020.md`。
- 首页/日内/设置页 200；旧 10/19 收市观察计划 200。10/20 和 10/21 尚未收市的旧计划返回预期结构化 409；新日内计划两个日期均 configured。
- JS `index-CQFXFf8n.js`：371,156 B，SHA256 `b653b33b3883c50b85d07b8bb8f8ab43a2e7870292e6c80fa8b372fbb48065a1`。
- CSS `index-DWN1bHP7.css`：159,915 B，SHA256 `e1770f438159e227e326aea9db727cddc4cb00a98d50b6082a2e03a1e707035d`。
- 服务完整 body 与本地构建长度、哈希一致，新 JS 包含 H 图/差分图/柱体标记代码；公网 `https://urus.yimeinforge.com/intraday` 已引用两个新资源。
- `git diff --check` 通过。

## 未被本次修复的状态

盘中事件健康页仍保留上个交易时段的只读数据库历史告警（status=error/stale），本次未清理或改写该状态，也不能把 GET 200 当作告警已恢复。收盘旧行情显示 stale，CTA 输入不足显示 unavailable；这些边界已在原部署报告和本次独立报告保留。本次仅验证双图 UI 上线和原接口契约，不能代替开盘后新采集窗口的验证。

私密运行环境和执行日志保存在 `/private/tmp/urus-deploy-macd-overlay-20261020/`，报告不包含凭据。
