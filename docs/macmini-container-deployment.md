# Mac mini · Apple Container 部署

Urus 使用 Apple `container` CLI 构建 Linux ARM64 OCI 镜像，并在 Mac mini 上运行一个容器。容器包含 FastAPI、内置 Vue 前端、Alembic 和收市观察调度器；调度器调用容器内的 API，与后端共享同一个 `/data` 生命周期。

## 构建镜像

```bash
IMAGE_TAG=urus-$(date +%Y%m%d-%H%M) scripts/build_apple_container_image.sh
```

脚本会把 OCI 镜像和同名 metadata `.env` 写入 `artifacts/container-images/`。

## 私有运行配置

```bash
cp deploy/urus.container.env.example deploy/urus.container.env
chmod 600 deploy/urus.container.env
```

至少配置：

- 可达的 `MOOMOO_HOST` 和 `MOOMOO_PORT`；如果 OpenD 在宿主机，不要使用容器内的 `127.0.0.1`；
- `MOOMOO_ENABLED=true`；
- 是否启用 FRED/Yahoo；
- 如需远程 AI 仲裁，再配置 `ANOMALO_WORKFLOW_*`。Token 只放 Secret Store，不写进仓库或前端。

部署前从 Mac mini 验证 OpenD 的 `11111` 端口可达。

## 部署

```bash
REMOTE=macmini \
ENV_FILE=deploy/urus.container.env \
scripts/deploy_apple_container.sh \
  artifacts/container-images/urus-<tag>-linux-arm64.env
```

默认使用 4 CPU、2 GiB 内存，可通过 `CONTAINER_CPUS` 和 `CONTAINER_MEMORY` 覆盖。页面默认地址为 `http://<macmini>:7777`，健康检查为 `/api/health`。宿主机持久化目录默认是 `~/data/urus`。

如果确实需要把本地数据库迁移到 Mac mini，才显式传入：

```bash
DATABASE_FILE=backend/urus.db
```

部署脚本会先备份远端数据库，再替换它。不要在本地后端仍写入 SQLite 时传输数据库。

## 持久化目录

- `urus.db`：Universe、canonical 日线、冻结 Dataset、Observation Run、期权结果和远程决策记录；
- `moomoo_home/`：Moomoo SDK 运行目录；
- `moomoo_collection.lock`：主机级 OpenD 锁和限频时间戳；
- `scheduled_collection/`：收市观察调度日志、去重状态和进程锁。

删除或升级容器不会删除这些文件；重新挂载同一目录即可继续使用。Alembic 只向前迁移外部数据库。

## 上线检查

1. `container list` 中只有当前 `urus` 容器在运行。
2. `curl http://127.0.0.1:7777/api/health` 返回 `status=ok`。
3. `/api/settings` 显示收市观察开关，且 `/api/settings/universe` 返回预期 Universe。
4. 执行一次 `post_close_observation`，确认股票日线、期权快照、Max Pain/DEX 邻近标记和质量状态可见。
5. `container logs urus` 没有迁移、SQLite lock、OpenD 或限频错误。
6. 如启用远程 AI，从个股/组页面发起一次预检，确认输入只来自指定冻结证据。

## 当前边界

- 没有登录认证，只适合可信 LAN；公网部署必须增加认证、访问控制和密钥管理。
- SQLite 只支持一个 Urus backend 写入实例。
- 调度器按 `exchange-calendars` 过滤周末、节假日和提前收市。
- 系统是研究与决策辅助，不包含下单、仓位限制和自动风控。
