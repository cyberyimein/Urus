# Mac mini · Apple Container 部署

Urus 参考相邻 Anomalo 工程，使用 Apple `container` CLI 构建 Linux ARM64 OCI 镜像、通过 SSH 复制到 Mac mini，再运行两个容器：

- `urus`：FastAPI、内置 Vue 生产前端、Alembic 和 SQLite；
- `urus-scheduler`：东京时间调度器，仅调用 `urus` API，共享持久化 `/data`。Apple Container 当前不会为同一自定义网络自动提供可用的容器名 DNS，因此部署脚本从 `container inspect` 读取 backend IPv4 后注入调度器 URL。

## 构建

```bash
IMAGE_TAG=urus-$(date +%Y%m%d-%H%M) scripts/build_apple_container_image.sh
```

输出 OCI tar 和同名 metadata `.env` 到 `artifacts/container-images/`。

## 私密运行配置

```bash
cp deploy/urus.container.env.example deploy/urus.container.env
chmod 600 deploy/urus.container.env
```

填写 OpenRouter 密钥以及容器可访问的 OpenD 地址。若 OpenD 位于 Mac mini 宿主机，不能写 `127.0.0.1`；应按 Apple Container 的宿主机 DNS/网络配置使用可达主机名。若仍位于 `opend-host`，部署前应从 Mac mini 验证该地址的 `11111` 端口。

## 部署

```bash
REMOTE=macmini \
ENV_FILE=deploy/urus.container.env \
scripts/deploy_apple_container.sh \
  artifacts/container-images/urus-<tag>-linux-arm64.env
```

需要把当前开发数据库一并迁移时，显式增加：

```bash
DATABASE_FILE=backend/urus.db
```

部署脚本会先把远端已有数据库复制成带时间戳的备份，再替换数据库。不要在本地后端仍写入 SQLite 时传输数据库。

默认页面为 `http://<macmini>:7777`，健康检查为 `/api/health`。容器内部仍监听 `8000`，仅在 Mac mini 宿主机映射为 `7777`；这样不会占用现有 Anomalo 的 `8000`。持久化目录默认是远端 `~/data/urus`。

该宿主机目录包含：

- `urus.db`：运行、冻结数据集、技术报告、AI 报告/trace、运行设置和 Universe 版本；
- `moomoo_home/`：Moomoo SDK 运行目录；
- `scheduled_collection/`：调度日志、去重状态和进程锁。

删除或升级容器不会删除这些文件。部署脚本重新挂载同一目录，Alembic 只对外部数据库执行向前迁移。

## 今晚上线前必须通过

1. `container list` 同时看到 `urus` 与 `urus-scheduler`。
2. 在 Mac mini 上执行 `curl http://127.0.0.1:7777/api/health` 返回 `status=ok`。
3. `/api/settings` 显示 `ai_decision_enabled=true`、`openrouter_configured=true`。
4. 运行设置为盘前 AI、尾盘只采集、收盘复盘 AI。
5. Universe 标的、CTA、期权和 AI 候选范围正确。
6. 在 Mac mini 手动触发一轮分析，确认 OpenD、期权、OpenRouter 均成功且 AI 报告不是占位。
7. 查看 `container logs urus` 与 `container logs urus-scheduler`，确认无迁移、SQLite lock、OpenD 或模型错误。

## 当前边界

- 页面没有登录控制，只能放在可信 LAN；公网部署前必须添加认证。
- SQLite 只支持一个 Urus backend 写入实例，不能横向扩容。
- 调度器过滤周末，但尚未识别美股节假日和临时休市。
- 系统是研究和决策辅助，不包含下单、仓位限制和自动风控，不能无人值守执行交易。
