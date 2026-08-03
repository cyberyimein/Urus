# Anomalo 预设 Agent 集成说明

本文面向需要调用 Anomalo AI 能力的外部程序，例如股票分析系统、新闻摘要服务或自动化任务。

预设 Agent 的作用是把一组稳定的 Agent 配置保存下来。外部程序只需要知道预设 Agent 的 `name`
或 `id`，不需要重复传递系统提示词、模型和工具配置。

## 一、基本概念

一个预设 Agent 由以下字段组成：

| 字段 | 是否必填 | 作用 |
| --- | --- | --- |
| `name` | 是 | Agent 的可读名称，也是外部程序可以使用的调用名称。不能包含 `/`。建议使用稳定的英文短名，例如 `fomc-brief`。 |
| `description` | 否 | 对 Agent 用途的说明，只用于管理界面和给人阅读，不会自动注入模型上下文。 |
| `ghost` | 否 | Agent 卡片上的头像或 Emoji 标识，只用于界面展示，不影响模型行为。 |
| `system_prompt` | 是 | Agent 的核心行为定义。这里描述角色、工作范围、证据要求、输出规则和禁止事项。 |
| `model` | 是 | OpenRouter 使用的模型 ID，例如 `deepseek/deepseek-v4-flash`。 |
| `temperature` | 否 | 采样温度，范围是 `0` 到 `2`，默认 `0.4`。事实摘要通常使用较低值。 |
| `tool_names` | 否 | Agent 可以使用的工具名称列表。空列表表示不提供工具。 |

最重要的是 `system_prompt`。`description` 只是目录信息，不应该把真正的业务约束只写在
`description` 里。

一个适合股票新闻 FOMC 摘要的配置可以是：

```text
You are a focused macro-news analyst for a stock research system.

Your task is to identify the latest Federal Open Market Committee decision from reliable sources
and summarize only the decision and its immediate policy implication. Use web tools when current
information is required. Do not invent rates, dates, votes, or forward guidance. If the evidence is
ambiguous, say so.

When the caller requests structured output, follow the requested response schema exactly. Keep the
final answer concise and do not include hidden reasoning.
```

## 二、在网页界面手动设置

1. 打开 Anomalo 的 **Preset Agents** Tab。
2. 点击 **New agent**。
3. 填写 `Name`、`Description` 和 `System prompt`。
4. 填写 OpenRouter 模型 ID，例如 `deepseek/deepseek-v4-flash`。
5. 在 **Available tools** 中勾选需要的工具。
6. 点击 **Save agent**。

保存后，编辑页面会显示：

- 按名称调用的接口，例如 `POST /api/agents/fomc-brief/chat`；
- 稳定的 Agent ID，例如 `agent_...`。

建议外部程序长期保存 `id`。`name` 适合阅读和配置文件，`id` 不会因为 Agent 改名而变化。

## 三、由外部程序创建或更新 Agent

如果希望由部署脚本、股票系统或另一个管理程序自动设计 Agent，可以调用管理 API。管理 API
需要管理员令牌，令牌放在服务端环境变量 `ANOMALO_ADMIN_TOKEN` 中。

管理请求头：

```http
X-Anomalo-Admin-Token: <ANOMALO_ADMIN_TOKEN>
Content-Type: application/json
```

管理员令牌不要放进浏览器前端、股票系统客户端或提交到代码仓库。适合的做法是让外部系统的
服务端调用管理 API，或者继续由人工在网页界面配置。

### 3.1 查询现有 Agent 和默认值

```bash
curl -fsS \
  -H "X-Anomalo-Admin-Token: $ANOMALO_ADMIN_TOKEN" \
  "$ANOMALO_BASE_URL/api/manage/agents"
```

返回内容包含 `agents` 和 `defaults`。`agents` 中每个对象包含 `id`、`name`、`description`、
`system_prompt`、`model`、`temperature` 和 `tool_names`。

### 3.2 创建 Agent

```bash
curl -fsS -X POST \
  -H "X-Anomalo-Admin-Token: $ANOMALO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  "$ANOMALO_BASE_URL/api/manage/agents" \
  -d '{
    "name": "fomc-brief",
    "description": "Summarizes FOMC decisions for the stock research system.",
    "ghost": "📈",
    "system_prompt": "You are a focused macro-news analyst. Use reliable sources, do not invent facts, and summarize the FOMC decision concisely.",
    "model": "deepseek/deepseek-v4-flash",
    "temperature": 0.2,
    "tool_names": ["web_search", "web_fetch"]
  }'
```

创建成功后，响应中的 `agent.id` 就是该预设 Agent 的稳定 ID。`tool_names` 必须来自当前
Anomalo 可用工具；可以先调用 `GET /api/tools` 查看工具名称。

### 3.3 更新 Agent

更新接口使用完整定义，建议先读取当前配置，再修改需要变更的字段：

```bash
curl -fsS -X PUT \
  -H "X-Anomalo-Admin-Token: $ANOMALO_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  "$ANOMALO_BASE_URL/api/manage/agents/$AGENT_ID" \
  -d @agent-definition.json
```

更新 Agent 后，后续新调用会使用新配置。已经保存的会话仍然属于原来的 Agent ID。

### 3.4 删除 Agent

```bash
curl -fsS -X DELETE \
  -H "X-Anomalo-Admin-Token: $ANOMALO_ADMIN_TOKEN" \
  "$ANOMALO_BASE_URL/api/manage/agents/$AGENT_ID"
```

删除 Agent 不会删除已有的会话历史或 Stop/Resume 检查点。

## 四、外部程序调用 Agent

### 4.1 普通非流式调用

外部程序可以按名称或 ID 调用。下面的例子按名称调用：

```bash
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  "$ANOMALO_BASE_URL/api/agents/fomc-brief/chat" \
  -d '{
    "message": "查找最新 FOMC 决定，用一两句话总结。",
    "session_id": "stock-fomc-2026-08-03"
  }'
```

也可以把 URL 中的 `fomc-brief` 换成 `agent_...` ID。名称大小写不敏感。

响应主要字段：

| 字段 | 说明 |
| --- | --- |
| `agent` | 实际使用的 Agent ID 和名称。 |
| `session_id` | 当前会话 ID；没有传入时由 Anomalo 生成，外部程序应保存它。 |
| `events` | 本次运行的完整事件列表，包括工具调用和运行状态。 |
| `final_text` | 最终文本回答。 |
| `output` | 使用结构化输出时解析后的 JSON 值。 |
| `output_format` | `text`、`json_object` 或 `json_schema`。 |

### 4.2 结构化 JSON 输出

如果股票系统需要稳定解析结果，应在每次调用时传递 `response_format`。工具调用阶段仍然由
Anomalo 内部完成；工具调用结束后，Anomalo 会使用非流式 finalizer 生成最终结果，并校验结果
是否符合 Schema。

```bash
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  "$ANOMALO_BASE_URL/api/agents/fomc-brief/chat" \
  -d '{
    "message": "查找最新 FOMC 决定，并给出一句话总结。",
    "session_id": "stock-fomc-2026-08-03",
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "fomc_summary",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "decision": {"type": "string"},
            "summary": {"type": "string"}
          },
          "required": ["decision", "summary"],
          "additionalProperties": false
        }
      }
    }
  }'
```

当前支持：

- `text`：普通文本；
- `json_object`：JSON 对象；
- `json_schema`：按指定 JSON Schema 校验的结构化结果。

对 `json_schema`，Schema 应该使用本地定义，不要使用远程 `$ref`。校验失败时，Anomalo 会
自动重试一次；仍然失败会返回 `run.error`，并带有 `structured_output_invalid` 错误码。

### 4.3 流式调用

需要实时显示工具调用和回答过程时使用：

```bash
curl -N -X POST \
  -H "Content-Type: application/json" \
  "$ANOMALO_BASE_URL/api/agents/fomc-brief/chat/stream" \
  -d '{
    "message": "查找最新 FOMC 决定并总结。",
    "session_id": "stock-fomc-stream-1"
  }'
```

响应是 NDJSON，每一行是一个 Agent event。常见事件包括 `run.started`、`llm.request`、
`message.delta`、`tool.started`、`tool.finished`、`message.done` 和 `run.finished`。

### 4.4 Stop 和恢复

流式 HTTP 调用被客户端中断时，Anomalo 会把当前运行保存为该 `session_id` 的 checkpoint，
包括尚未返回结果的工具调用的 recovery result。下一次恢复时不要重新发送原始问题：

```bash
curl -fsS -X POST \
  -H "Content-Type: application/json" \
  "$ANOMALO_BASE_URL/api/agents/fomc-brief/chat" \
  -d '{
    "session_id": "stock-fomc-stream-1",
    "resume": true
  }'
```

恢复请求必须携带 `session_id`，不能同时切换到另一个 Agent。一个 `session_id` 一旦绑定某个
预设 Agent，再被其他 Agent 使用会返回 `409`。

## 五、推荐的外部股票系统封装

股票系统建议把 Anomalo 封装成一个内部客户端，至少保存以下信息：

```text
ANOMALO_BASE_URL       = https://agent.yimeinforge.com
ANOMALO_AGENT_ID       = agent_...
ANOMALO_AGENT_NAME     = fomc-brief
ANOMALO_SESSION_ID     = 每个任务自己的 session_id
```

一次独立的股票任务使用一个独立 `session_id`。同一个任务需要多轮追问时复用该 ID；新的任务
不要复用旧 ID，以免把上一任务的新闻上下文带入当前分析。

推荐把业务输出直接定义成 JSON Schema，例如：

```json
{
  "decision": "hold",
  "summary": "The FOMC kept the target range unchanged and signaled ..."
}
```

这样股票系统读取 `output` 即可，不需要解析 `final_text`，也不需要理解 Anomalo 内部的工具
调用事件。

## 六、部署和安全注意事项

- 预设 Agent 定义保存于 `ANOMALO_DATA_DIR/preset-agents.sqlite3`。
- 会话和 Stop/Resume 检查点保存于 `ANOMALO_DATA_DIR/sessions.sqlite3`。
- 替换容器时必须保留宿主机的数据目录。
- 管理 API 使用 `ANOMALO_ADMIN_TOKEN`，只应由可信服务端调用。
- 预设 Agent 调用接口与基础 Chat API 共用部署安全边界。当前不单独签发 Agent API Key；生产环境应通过反向代理、内网或 API Gateway 限制访问。
- 不要把管理员令牌放入股票客户端、浏览器代码或 Git。
