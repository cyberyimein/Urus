# 日内观察收盘后候选版本核对与 HTTP 回归清单（2026-10-17）

本记录由独立验收 agent 编写，范围是部署候选源码身份核对和部署后只读 HTTP 回归准备。root 负责部署；本轮没有修改业务实现、测试、配置、容器或数据库，也没有连接 SSH、OpenD 或真实行情 provider。

## 当前身份核对

### 既有最终验收证据

前一轮最终离线证据仍为：

| 范围 | 结果 | 证据 |
| --- | ---: | --- |
| backend | 551 passed | `docs/reviews/intraday-normalized-storage-acceptance-20261016.md`；日志 `/private/tmp/urus-predeploy-20261016/logs/backend-normalized-final-551.log` |
| urus-moomoo collector | 157 passed in 5.88s | `docs/reviews/intraday-predeploy-test-report-20261016.md`；日志 `/private/tmp/urus-predeploy-final-20261016/logs/collector-full-final.log` |
| frontend | vue-tsc + Vite，84 modules | `docs/reviews/intraday-normalized-storage-acceptance-20261016.md` |

本轮没有重复上述全套测试；这里的 551/157 是上轮已经记录的最终结果，不是本轮新执行的计数。

### Collector 源码 SHA-256

以下当前文件与上一轮 157 项最终报告中的身份全部一致：

```text
urus-moomoo/src/urus_moomoo/runtime.py
  22666a18b37178be292292f8190300ff3735fe1255d0926316c824c6e5f20f46
urus-moomoo/src/urus_moomoo/services/scheduler.py
  a0957290c8628a06d0101580fdb16843ea8b5e28a0a91ed862c0ba24d9c74164
urus-moomoo/src/urus_moomoo/config.py
  be5d1e90ee70c570ed9f1c32d41dba706502a5f24c78763a268de25a3ad0dcf9
urus-moomoo/src/urus_moomoo/integrations/moomoo.py
  01fc2a167bc13c7a757630e13ad49cd87de9a45df2d0a53d40508cb3ad30a97f
urus-moomoo/src/urus_moomoo/storage/sqlite.py
  2e5fb90a01c504631b5dff0501f2c67b250b6be034f7cc5db729c0bcb7e49287
urus-moomoo/src/urus_moomoo/services/intraday_equities.py
  972694fba1e77da79c0d26966b73cafca348496035ecbe887a24be348c4d4cab
urus-moomoo/src/urus_moomoo/storage/equity_history.py
  50fe36b7eed26ea42e6b49b007ccdfccce12f6117d0b864b02f3e1e4ab847e0d
```

### 当前候选镜像源清单核对

候选源清单：`/private/tmp/urus-intraday-image-20261016-1126/source-manifest.json`。

```text
manifest SHA-256:
20c681f15088570e2cdf51fd1ac49ffd660e4d3bdcf5bfa737ddf3a4ae56bbd2
```

在 S4–S8 白名单修复前，以下 16 个当前文件逐项与清单比较，结果为 **16/16 PASS**。随后 root 让开发对 `backend/app/api/intraday.py` 做了最小白名单修复；修复后的当前文件与旧清单自然为 **15/16 PASS**，唯一差异就是该预期变更，其他 15 个文件仍一致：

```text
backend/app/api/router.py
backend/app/api/intraday.py
backend/app/api/systematic_risk.py
backend/app/core/config.py
backend/app/main.py
frontend/src/api/client.ts
frontend/src/api/intraday.ts
frontend/src/api/systematicRisk.ts
frontend/src/components/IntradayEventPanel.vue
frontend/src/components/SystematicRiskCard.vue
frontend/src/router/index.ts
frontend/src/views/IntradayObservationView.vue
frontend/src/styles/intraday.css
frontend/src/types/intraday.ts
frontend/src/types/intradayEvents.ts
frontend/src/types/systematicRisk.ts
```

修复前 `backend/app/api/intraday.py` 的清单 SHA 为 `e9ec23c4ab05f22a5f789f25d7d7e81d53dc12a8305a0c6a585931e7be18a51a`。当前修复后的 SHA 为：

```text
backend/app/api/intraday.py
  8266b8aab58d985b86a267f9bbcb75c0685dc9a56976448bcad7150a8782a876
```

部署候选的 source manifest 需要由 root/构建流程更新或明确记录这一变更；本 agent 不重建镜像、不部署。

其余当前重点文件的候选清单 SHA 与实际值相同：

```text
backend/app/api/router.py                         707b27b462af4f1d8ecd5f10936b4b04ac9640e576cbd1e201cc4d9188ecefbd
backend/app/api/intraday.py                       8266b8aab58d985b86a267f9bbcb75c0685dc9a56976448bcad7150a8782a876 (S4–S8 修复后)
backend/app/api/systematic_risk.py                ec4322839ed811363cb1d0e712413efd314db286beaaf71698724d15c93ab8c4
backend/app/core/config.py                        0c5380abc34bbb478af8b3ac9114eafe47fcd324e9a51f4829133664ba66755d
backend/app/main.py                               d1757b1bfa5d279fa4ba3d25bb269dfaaf92549ec2133ddef359bcf72ac08c01
frontend/src/api/client.ts                        38a52239bcd31a8c8af82bc670d15897dccf3a2a0ed89f2d683988657b6831f6
frontend/src/api/intraday.ts                      7a1c7333bf990ae92bf1b1b2e24e1e5895fa2b212fc54997bb1a80133997c6a2
frontend/src/api/systematicRisk.ts                ac20a92f1a22c8bc4171954b3bfdf5251f70a6f734ab5e0c79d7cbcc4e80fc00
frontend/src/components/IntradayEventPanel.vue    410dd58bc1be025f1e93631e21cd07e7b1b4209c2a1889a154f4d37984b8ac1c
frontend/src/components/SystematicRiskCard.vue    165e16045d768f572859a228cb01fe33bf91328d97a9308dda370504ae3004f3
frontend/src/router/index.ts                       981c26ca06464698f9ada14134084299a65af70a9a07b076f8b9da615e19f365
frontend/src/views/IntradayObservationView.vue    1f0225df04ae37e7ee9ead063c1740021904da06d5e67c427a6ccef8a2a451db
frontend/src/styles/intraday.css                   da064109a8907220042920419c0de65c0c0c601f829ecf0e5f5beb8d41df89c4
frontend/src/types/intraday.ts                    23557a539a86f7107b2222eea5621b0f59848aaba696eb9bdc9e8b42f8c9050f
frontend/src/types/intradayEvents.ts              ceab2e5780c26536c4e8f986d6ff5ad4ca005db2b7077c6393eb2fcaeb8b4790
frontend/src/types/systematicRisk.ts              039b0d06c6bcafca27325a276cad493e947e4ecb7bae35631fbf2498ef309c7c
```

### S4–S8 策略筛选定向复验

root 已把 API 白名单从 `S1/S2/S3` 扩展到 `S1`–`S8`，并保留未知 `S9` 的结构化 400 拒绝。新增测试使用 `test_intraday_event_retention._event` 创建真实 ORM 事件行，给目标事件写入完整 `payload_json`（包含 `strategy` 和 `setup_id`），同时保留另一条不同策略的 seed event；因此不是空 payload 的理想 fixture。

```text
cd /Users/waynewong/code/Urus/backend
../backend/.venv/bin/pytest -q tests/test_intraday_event_strategy_filters.py
6 passed
```

覆盖 `S4`、`S5`、`S6`、`S7`、`S8` 和大小写输入 `s8`。每个请求都断言只返回对应策略的事件，另断言 `S9` 返回 400。测试只使用临时 SQLite 和 FastAPI `TestClient`，未连接 provider 或生产数据库。

### 已发现的待 HTTP 核对点

本地定向复验已确认 `S4`–`S8` 可筛选；部署后仍必须实际请求 `S4`、`S8` 和未知 `S9`，确认运行中的服务加载了修复后的源身份：`S4/S8` 应为 200 JSON，`S9` 应为 400 JSON。若 `/api/version` 或容器 source identity 仍对应旧 manifest，应先报告候选版本不一致，不把旧服务的 400 误判为当前实现行为。

## 部署后只读 HTTP 回归

服务可用后执行。HTTP 检查只使用 `GET`，不调用采集、刷新、写入、容量计划、同步、运行或删除接口。每次响应都同时断言 HTTP 状态、`Content-Type: application/json` 和 JSON 结构；API 路径返回 SPA HTML 即失败，即使 HTTP 状态为 200。

### 通用安全检查

| 检查 | 通过条件 |
| --- | --- |
| 网络范围 | 仅 root 指定的本机/已授权只读 HTTP 地址；不访问 OpenD、provider、SSH 或远程数据库 |
| 方法范围 | 只发 GET；不调用 POST/PUT/DELETE、手动采集、refresh、sync、capacity-plan、run、rerun |
| 内容类型 | 所有 `/api/*` 响应为 JSON；不能把前端 fallback HTML 当成功 |
| 读取行为 | 请求前后只比较返回的身份和状态；不直接写入或修复数据库，不清理数据 |
| 错误行为 | unavailable/disabled/no_data/invalid_source/error 必须是结构化 JSON，不能 500 HTML 或静默借旧槽 |
| 时间行为 | 所有时间字段能解析为 ISO 时间；事件、报价、bars 不出现未来数据、`NaN` 或 `undefined` |

### 既有功能只读回归

执行顺序使用根服务当前返回的真实 identity，不在命令中伪造 dataset、run 或 revision id。

| Endpoint | 断言 |
| --- | --- |
| `GET /api/health` | 200 JSON，健康状态字段存在；不因新增功能启动失败 |
| `GET /api/health/database` | 200 JSON，数据库健康/迁移身份可读；不触发迁移或建表 |
| `GET /api/version` | 200 JSON，版本/构建身份存在；记录部署版本 |
| `GET /api/settings/universe` | 200 JSON；`version_id`、`content_sha256`、enabled symbols、capacity/collection states 存在，符号集合与部署前只读基线一致 |
| `GET /api/settings/universe/versions?limit=20` | 200 JSON 列表；当前版本可在列表中定位，历史身份不被新日内功能改写 |
| `GET /api/observation/groups` | 200 JSON 列表；active group 的 `version_id`、symbols、`content_sha256` 存在，与 Universe/上一轮基线一致 |
| `GET /api/observation/collection-plan?trading_date=<XNYS真实交易日>` | 200 JSON；`schema_version=urus.observation.collection_plan.v1`、`plan_id`、`trading_date`、`group_ids`、`group_version_ids`、`market_symbols`、`options_symbols` 存在；不发 provider 请求 |
| `GET /api/observation/indicator-catalog` | 200 JSON 列表；既有指标仍可读，MACD 三层注册项可定位时记录其 identity |
| `GET /api/observation/strategy-catalog` | 200 JSON 列表；既有策略目录未被日内功能替换 |
| `GET /api/daily-evidence/datasets/<真实dataset_id>` | 200 JSON，或真实缺失时结构化 404；不凭空创建 dataset；数据集 identity/hash 可读 |
| `GET /api/daily-evidence/datasets/<真实dataset_id>/chart` | 200 JSON，或真实缺失时结构化 404；chart hash、dataset identity、最后日线日期和 MACD 三层数据一致 |
| `GET /api/daily-evidence/datasets/<真实dataset_id>/instruments/<symbol>/evidence` | 200 JSON，或真实缺失时结构化 404/422；symbol 规范化、旧字段和新差分字段不混淆 |
| `GET /api/daily-evidence/datasets/<真实dataset_id>/strategies` | 200 JSON，或数据集确实没有策略决策时结构化 404；不触发重新计算 |

`<真实dataset_id>`、`<symbol>` 和交易日均从 GET 响应或 root 提供的只读基线中取得。不会用 POST 创建数据集来“帮助”回归通过。

### 新日内行情只读回归

| Endpoint | 断言 |
| --- | --- |
| `GET /api/intraday/overview` | 200 JSON；启用时检查 `schema_version`、`formula_version`、`source.capture_id/content_sha256/trading_date/scheduled_at/cutoff`、`freshness`、`coverage`、`symbols`、`breadth`、`warnings`；source 是最新槽，不能把旧 complete 槽伪装成当前 partial；关闭或无源时允许 `disabled`/`no_data`/`invalid_source`，但必须结构化 |
| `GET /api/intraday/symbols/<symbol>` | 200 JSON；symbol 大小写归一化；`source.capture_id` 与 overview 相同；`role`、quote、5m bars、`features`、`reasons`、`warnings` 存在；bar 的 OHLCV 合法、时间不晚于 cutoff、缺尾部时显示 stale/unavailable 而不借旧值 |
| `GET /api/intraday/symbols/<unknown>` | 200 JSON `status=not_found` 或明确 unavailable；不 500、不用其他 symbol 的数据填充 |
| 前端 `/intraday` 对应接口 | 页面读取只调用 overview；每 60 秒刷新行情，不产生采集请求；股票角色 A/B/C/D、选择标的、5m 蜡烛/MA5、MACD H/ΔH/Δ²H tabs 和过零 marker 的数据来源与 overview 同一 capture |

### 事件、CTA 和健康状态只读回归

| Endpoint | 断言 |
| --- | --- |
| `GET /api/intraday/health` | 200 JSON；`enabled/status/cursor/backlog_remaining/last_error/last_evaluated_at/age_seconds/stale/stale_after_seconds` 完整；`stale_after_seconds=600` 时不显示 NaN；关闭时为结构化 disabled，不启动消费者 |
| `GET /api/intraday/events?limit=2` | 200 JSON；初次无 `after_id` 返回当前筛选条件尾部并按 id 升序；`events` 的 id 单调、`current_status` 可与历史 status 不同；候选/确认/失效/过期和冻结线字段可读 |
| `GET /api/intraday/events?after_id=0&limit=2` | 200 JSON；从头分页，`next_after_id/has_more` 与 id 递进一致，不漏不重 |
| `GET /api/intraday/events?after_id=<previous>&limit=2` | 200 JSON；持续 cursor 只向前，不回放旧事件；同 setup 的 current status 与最新迁移一致 |
| `GET /api/intraday/events?symbol=<symbol>&status=candidate` | 200 JSON；筛选只影响返回列表；若后续同 setup 已确认/失效，返回项的 `current_status` 和冻结线仍更新 |
| `GET /api/intraday/events?strategy=S1` … `S8` | 200 JSON；修复后八个已实现策略过滤均应可用，空列表只能说明当前没有事件 |
| `GET /api/intraday/events?strategy=S9` | 400 JSON；未知策略必须被拒绝，不接受 HTML/500 |
| `GET /api/systematic-risk/overview` | 200 JSON；关闭时 `status=disabled`；启用且有数据时检查 SPY/QQQ/IWM 的 baseline identity、quote freshness/price_basis、三种 SMA 模型、scenario、`trend_only/volatility_only/joint/interaction` 分解和由多转空/由空转多方向；D 组 stale 不得屏蔽健康 ETF；数据不满足时为结构化 unavailable/error |
| 前端 `/intraday` CTA 卡片 | 只读 `/api/systematic-risk/overview`，每 60 秒刷新；显示 pp 分解、模型不足/价格口径原因和基线身份，不出现 NaN/undefined；事件面板每 15 秒只读 events/health |

## 执行记录

root 已提供部署后的只读地址 `http://192.168.31.31:7777`。本轮只发送 GET；没有调用刷新、采集、provider、POST、PUT 或 DELETE。原始响应摘要和临时证据保存在 `/private/tmp/urus-postclose-http-20261017/`。

### 源码与服务版本

部署候选清单为 `artifacts/intraday-release-20261017/source-manifest.json`：

```text
manifest SHA-256: af638e14dc226642e3b78cee93127e61e8d1ecc24dde25ea0ac2d769221928c2
entries: 249
current source comparison: 249/249 matched
```

`GET /api/version` 返回 HTTP 200 JSON，`app_version=0.1.0`、`api_schema_version=v1`；`GET /api/health` 和 `/api/health/database` 均 HTTP 200 JSON，状态为 `ok`。

### 既有功能和页面

| GET | 结果 |
| --- | --- |
| `/api/settings/universe` | **200 JSON**；revision `12`、46 个 symbols、`content_sha256=0b0db5874d53933c3b4996fea0d987ddbf8f298da2437559abce153d5647a788` |
| `/api/settings/universe/versions?limit=1` | **200 JSON**；当前 version 与 Universe content hash 可定位 |
| `/api/observation/groups` | **200 JSON**；10 个 group，全部 active |
| `/api/observation/collection-plan?trading_date=2026-10-16` | **200 JSON**；schema 正确，10 个 group、46 个 market symbols、19 个 options symbols，plan id `ba7101db4180da4778597965153d88833ed2ca79fdf25334bc20a618b7a8b13d` |
| `/api/observation/indicator-catalog` | **200 JSON**；10 项，包含 `macd_histogram`、`macd_histogram_delta`、`macd_histogram_delta2` |
| `/api/observation/strategy-catalog` | **200 JSON**；5 项 |
| `/api/observation/run-summaries?limit=20` | **200 JSON**；最新 2026-10-16 run succeeded，10/10 groups succeeded |
| `/`、`/intraday` | **200 HTML**；两页引用同一 JS `/assets/index-0Qfk7xbk.js` 和 CSS `/assets/index-b2XcEKT8.css` |

从 run summary 取得真实 run `e30bde2d-90dd-492e-bfd6-87aeb2c8a2ca` 后，`GET /api/observation/runs/{run_id}` 返回 **200 JSON**；从其真实 snapshot 取得 dataset `7e6a72ef-a0c7-4c65-bc13-6427ca33b160`，没有创建或猜测 dataset。

### 日线功能

| GET | 结果 |
| --- | --- |
| `/api/daily-evidence/datasets/7e6a72ef-a0c7-4c65-bc13-6427ca33b160` | **200 JSON**；`status=ok`、trading date `2026-10-16`，dataset content hash `bb810789df1b99dac97c2367cf7373182a2f97e7c384e4729e6084a7c2196d91` |
| `/api/daily-evidence/datasets/{id}/instruments/SPY/evidence` | **200 JSON**；dataset id 与真实 run identity 一致，返回 chart 和 strategy payload |
| `/api/daily-evidence/datasets/{id}/chart` | 普通完整 GET 发生传输中断，见下方；未将部分响应判为通过 |
| `/api/daily-evidence/datasets/{id}/strategies` | 普通完整 GET 发生传输中断，见下方；未将部分响应判为通过 |

### 新日内、事件和 CTA

| GET | 结果 |
| --- | --- |
| `/api/intraday/overview` | **200 JSON**，`status=no_data`；sidecar 尚无盘中股票采集槽，`quality.status=no_data`、`minute_timestamp_boundary_verified=false`，符合采集器尚未切换时的预期 |
| `/api/intraday/symbols/SPY` | **200 JSON**，`status=no_data`；没有伪造 bars/features |
| `/api/intraday/symbols/NOT_A_REAL_SYMBOL` | **200 JSON**，`status=no_data`；无源时明确返回 no_data，不借其他标的数据 |
| `/api/intraday/health` | **200 JSON**，`enabled=true`、`status=idle`、`cursor=null`、`stale=false`、`stale_after_seconds=600`；没有误报旧处理时间 |
| `/api/intraday/events?limit=2` | **200 JSON**，enabled、status=ok、空 events |
| `/api/intraday/events?strategy=S4&limit=2` | **200 JSON**，status=ok、空 events；白名单修复已加载 |
| `/api/intraday/events?strategy=S8&limit=2` | **200 JSON**，status=ok、空 events |
| `/api/intraday/events?strategy=s8&limit=2` | **200 JSON**，status=ok、空 events；大小写输入可用 |
| `/api/intraday/events?strategy=S9&limit=2` | **400 JSON**，`detail="strategy must be S1 through S8"`；未知策略被拒绝 |
| `/api/systematic-risk/overview` | **200 JSON**，`status=unavailable`，reason=`capture_unavailable`；SPY/QQQ/IWM 均结构化 `quote_unavailable`。sidecar 尚无盘中报价，因此 CTA 不伪造 baseline/scenario，符合当前阶段预期 |

### 静态资源传输异常

应用 HTML 和 asset URL 正确，但普通完整 GET 的响应存在 Content-Length 与实际传输长度不一致，并由服务端提前关闭连接：

| Resource | 普通 GET 观察 |
| --- | --- |
| JS `/assets/index-0Qfk7xbk.js` | Python 3 `urllib.request.urlopen` 收到 242748 bytes，`IncompleteRead` 还差 114496（总长 357244）；`/usr/bin/curl --http1.1` 两次分别收到 348788（还差 8456）和 236460（还差 120784），均报 curl(18) |
| CSS `/assets/index-b2XcEKT8.css` | Python urllib 收到 131396 bytes，`IncompleteRead` 还差 23082（总长 154478）；curl 收到 131503（还差 22975），报 curl(18) |
| icon `/urus-icon.png` | Python urllib 收到 3040052 bytes，`IncompleteRead` 还差 160766（总长 3200818）；Range probe 返回 HTTP 206，PNG 签名 `89504e470d0a1a0a` |

同类问题也出现在大 JSON 响应。Python 3 `urllib.request.urlopen` 的实际记录如下：

| Resource | 收到 / 期望 | 结果 |
| --- | ---: | --- |
| `/api/settings/universe/versions?limit=20` | 131620 / 183950 bytes | `IncompleteRead`，还差 52330 |
| `/api/observation/runs?limit=20` | 14369780 / 14592061 bytes | `IncompleteRead`，还差 222281 |
| `/api/observation/runs?limit=1` | 232060 / 606859 bytes | `IncompleteRead`，还差 374799；对应的具体 `GET /api/observation/runs/e30bde2d-90dd-492e-bfd6-87aeb2c8a2ca` 可完整返回 606857 bytes |
| `/api/daily-evidence/datasets/7e6a72ef-a0c7-4c65-bc13-6427ca33b160/chart` | 16184660 / 16729943 bytes | `IncompleteRead`，还差 545283 |
| `/api/daily-evidence/datasets/7e6a72ef-a0c7-4c65-bc13-6427ca33b160/strategies` | 601436 / 867657 bytes | `IncompleteRead`，还差 266221 |

完整 GET 的客户端与请求条件：Python 3 `urllib.request.urlopen` 使用 `GET`、`Accept: application/json` 或 `*/*`、`Accept-Encoding: identity`；curl 使用 `/usr/bin/curl --http1.1 --silent --show-error --max-time 45 -H 'Accept-Encoding: identity' -H 'Connection: close'`。没有使用 httpx。静态资源响应头的一次记录为 `HTTP/1.1 200 OK`、`server: uvicorn`、正确的 `content-type`、`accept-ranges: bytes`、正确的 `content-length` 和 `connection: close`。

网络路径说明：最初未升级权限的 `functions.exec` sandbox 对目标 socket 直接返回 `URLError(PermissionError(1, 'Operation not permitted'))`，请求尚未到服务；升级权限后才得到上述 HTTP 响应。升级后的 Python/curl 进程没有 `HTTP_PROXY`、`HTTPS_PROXY` 或 `NO_PROXY` 环境项，因此不是显式代理配置造成的。root 按相同请求条件进一步定位：LAN 地址配合 `Connection: close` 可稳定复现截断（JS 236460/357244、CSS 131072/154478）；默认 keep-alive 完整，macmini localhost:7777 在 close 条件下完整，API 容器 127.0.0.1:8000 的 urllib 请求也完整。问题边界是 LAN + close 连接路径，而不是应用静态文件内容。

为区分源文件错误和传输问题，我使用只读 HTTP Range 分片重建两个核心 asset：

```text
JS: 357244 bytes，SHA-256 7ee5226783e8c787670034af22905e81418c269e23fc3380f49d9461e59900e2
CSS: 154478 bytes，SHA-256 89a706acc68c6fda62936b6becfe19833057112e17735393e61447796dec7356
```

两者都与当前 `frontend/dist/assets/` 的本地构建文件完全相同，说明候选 asset 内容正确。root 的复验记录 `/private/tmp/urus-postclose-20261017/transfer-root-results.json` 显示默认 keep-alive 下工作机 LAN 的 JS/CSS 三轮完整下载、hash 与 `frontend/dist` 一致，daily chart `16729943` bytes、strategies `867657` bytes 完整 JSON，8/8 请求 exit 0；macmini localhost:7777 在 close 条件下 JS/CSS 也完整，API 容器 127.0.0.1:8000 的 urllib 请求完整。因而应用和默认网页路径部署可用；LAN + `Connection: close` 的截断保留为后续网络兼容排查项，不修改应用或重启服务。

### 本轮执行结论

- API health、Universe、groups、collection-plan、catalog、真实 daily dataset/SPY evidence、intraday no-data、事件 S4/S8/s8/S9 和 systematic-risk 结构化降级均符合预期。
- 在股票采集尚未开始时，intraday `no_data`、event 空列表、CTA `unavailable/capture_unavailable` 是预期状态，不代表数据采集成功或 CTA 已有信号。
- 本验收工具的 close 请求路径以及 root 的 LAN + close 复验均出现大响应提前关闭；默认 keep-alive、macmini localhost 和 API 容器回环均完整通过。当前结论是部署功能通过，但 LAN + close 的传输兼容问题需要后续网络侧排查；本轮不扩展设计、不重启服务、不重跑 EOF。
- 本轮没有重复 backend/collector 全量，没有写入生产数据库，也没有发起采集或 provider 请求。

### 采集器切换后的有限复核

root 确认采集器切换完成、preflight 通过，且四个新股票表初始为 0 行；随后只重复读取新接口一次，没有做扫描或负载测试：

| GET | 切换后结果 |
| --- | --- |
| `/api/intraday/overview` | **200 JSON**，仍为 `status=no_data`；在首个股票槽尚未落盘时属于预期 |
| `/api/intraday/symbols/SPY` | **200 JSON**，`status=no_data` |
| `/api/intraday/health` | **200 JSON**，`enabled=true`、`status=idle`、无旧 cursor |
| `/api/intraday/events?limit=2` | **200 JSON**，空 events |
| `/api/intraday/events?strategy=S4/S8/s8` | 均 **200 JSON**，空 events；修复后的白名单仍生效 |
| `/api/intraday/events?strategy=S9` | **400 JSON**，未知策略拒绝 |
| `/api/systematic-risk/overview` | **200 JSON**，`status=unavailable`；尚无盘中报价，未伪造 CTA 信号 |

切换后状态与 root 的只读表计数一致：当前尚未产生股票 bars/quotes，因此不能把 `no_data` 解释成采集成功；下一次验收应在首个真实槽落盘后再检查 source/cutoff、5m bars、MA5/MACD 和事件 cursor。

当前可复现的源码核对命令（只读）为：

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path

root = Path('/Users/waynewong/code/Urus')
manifest = json.loads(Path('/private/tmp/urus-intraday-image-20261016-1126/source-manifest.json').read_text())
paths = [
    'backend/app/api/router.py', 'backend/app/api/intraday.py',
    'backend/app/api/systematic_risk.py', 'backend/app/core/config.py',
    'backend/app/main.py', 'frontend/src/api/client.ts',
    'frontend/src/api/intraday.ts', 'frontend/src/api/systematicRisk.ts',
    'frontend/src/components/IntradayEventPanel.vue',
    'frontend/src/components/SystematicRiskCard.vue',
    'frontend/src/router/index.ts', 'frontend/src/views/IntradayObservationView.vue',
    'frontend/src/styles/intraday.css', 'frontend/src/types/intraday.ts',
    'frontend/src/types/intradayEvents.ts', 'frontend/src/types/systematicRisk.ts',
]
for path in paths:
    actual = hashlib.sha256((root / path).read_bytes()).hexdigest()
    print('PASS' if manifest.get(path) == actual else 'FAIL', path)
PY
```

## 限制

- 本地 fake-provider 和上一轮离线测试不能证明本次部署容器已经加载了候选源码；因此 HTTP 回归必须先检查 `/api/version` 和 response JSON，而不能只看端口或 HTTP 200。
- 本轮已验证部署后的 source manifest、Universe/groups/collection-plan、真实日线 dataset identity 和 SPY evidence；由于 sidecar 尚无股票采集槽，尚未验证真实 5m bars/features、事件 cursor 的非空分页、quote freshness 或 CTA 的可用模型与价格口径。
- 额度、OpenD 连接、provider pacing 和生产数据完整性不在本次本地源码核对中重新声明；上一轮生产只读证据仍见 `docs/reviews/intraday-predeploy-production-check-20261016.md`。
