# OpenAI API 兼容性说明

本文档详细说明 AI Studio Proxy API 与官方 OpenAI API 的兼容性、差异和限制。

## 概述

AI Studio Proxy API 旨在提供与 OpenAI API 最大程度的兼容性，使现有使用 OpenAI SDK 的应用可以无缝切换到 Google AI Studio。但由于底层实现的差异（通过浏览器自动化访问 AI Studio Web UI），存在一些不可避免的差异和限制。

---

## 支持的端点

### ✅ 完全支持

| 端点 | 说明 | 兼容性 |
|------|------|--------|
| `POST /v1/chat/completions` | 聊天完成接口 | 完全兼容，支持流式和非流式 |
| `GET /v1/models` | 模型列表 | 完全兼容，返回 AI Studio 可用模型 |

### ⚠️ 部分支持

| 端点 | 说明 | 兼容性 |
|------|------|--------|
| `GET /health` | 健康检查 | 自定义端点，非 OpenAI 标准 |
| `GET /api/info` | API 信息 | 自定义端点，非 OpenAI 标准 |
| `GET /v1/queue` | 队列状态 | 自定义端点，非 OpenAI 标准 |
| `POST /v1/cancel/{req_id}` | 取消请求 | 自定义端点，非 OpenAI 标准 |

### ❌ 不支持

- `POST /v1/embeddings` - 嵌入向量生成
- `POST /v1/images/generations` - 图像生成
- `POST /v1/audio/transcriptions` - 音频转录
- `POST /v1/audio/translations` - 音频翻译
- `POST /v1/audio/speech` - 语音合成
- `POST /v1/files` - 文件上传管理
- `POST /v1/fine-tuning/jobs` - 微调任务

---

## `/v1/chat/completions` 端点详解

### 支持的请求参数

#### ✅ 完全支持

| 参数 | 类型 | 说明 | 备注 |
|------|------|------|------|
| `messages` | Array | 聊天消息数组 | 必需，支持 `system`, `user`, `assistant` 角色 |
| `model` | String | 模型 ID | 用于切换 AI Studio 中的模型 |
| `stream` | Boolean | 是否流式输出 | 支持 SSE (Server-Sent Events) 流式响应 |
| `temperature` | Number | 温度参数 (0.0-2.0) | 通过 Playwright 设置 AI Studio 页面参数 |
| `max_output_tokens` | Number | 最大输出 token 数 | 通过 Playwright 设置 AI Studio 页面参数 |
| `top_p` | Number | Top-P 采样 (0.0-1.0) | 通过 Playwright 设置 AI Studio 页面参数 |
| `stop` | Array/String | 停止序列 | 通过 Playwright 设置 AI Studio 页面参数 |

#### ⚠️ 部分支持

| 参数 | 类型 | 说明 | 限制 |
|------|------|------|------|
| `reasoning_effort` | String/Number | 思考模式和预算控制 | 自定义参数，支持 `"low"`, `"medium"`, `"high"`, 数值，`0` (关闭)，`"none"` (不限制) |
| `tools` | Array | 函数调用工具定义 | 支持 Google Search 工具，自定义工具支持有限 |
| `tool_choice` | String/Object | 工具选择策略 | 支持 `"auto"`, `"none"` |
| `response_format` | Object | 响应格式 | 部分支持，取决于 AI Studio 能力 |
| `seed` | Number | 随机种子 | 接受但可能不生效，AI Studio 不保证可重现性 |

#### ❌ 不支持或忽略

| 参数 | 说明 | 原因 |
|------|------|------|
| `frequency_penalty` | 频率惩罚 | AI Studio 不支持 |
| `presence_penalty` | 存在惩罚 | AI Studio 不支持 |
| `logit_bias` | Logit 偏差 | AI Studio 不支持 |
| `logprobs` | 返回 log 概率 | AI Studio 不支持 |
| `top_logprobs` | Top N log 概率 | AI Studio 不支持 |
| `n` | 生成多个回复 | AI Studio 不支持 |
| `user` | 用户标识符 | 接受但忽略 |

### 响应格式

#### 非流式响应

```json
{
  "id": "chatcmpl-1234567890-123",
  "object": "chat.completion",
  "created": 1699999999,
  "model": "gemini-2.5-pro",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "这是响应内容",
        "reasoning_content": "这是思考过程（如有）"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 50,
    "total_tokens": 60
  },
  "system_fingerprint": "camoufox-proxy"
}
```

#### 流式响应 (SSE)

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1699999999,"model":"gemini-2.5-pro","choices":[{"index":0,"delta":{"role":"assistant","content":"你好"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1699999999,"model":"gemini-2.5-pro","choices":[{"index":0,"delta":{"content":"！"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1699999999,"model":"gemini-2.5-pro","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}

data: [DONE]
```

---

## 主要差异

### 1. 响应延迟

**原因**: 通过浏览器自动化访问 AI Studio，存在额外的渲染和DOM操作开销。

**影响**: 
- 首字节时间 (TTFB) 比官方 API 长
- 流式响应的分块可能不如官方 API 细腻

**缓解措施**:
- 使用集成流式代理服务（默认启用，端口 3120）可显著减少延迟
- 三层响应获取机制确保在不同场景下的可用性

### 2. Token 计数

**原因**: 使用简化的 token 估算算法（基于字符数和 UTF-8 编码）。

**影响**:
- `usage` 字段中的 token 数量是估算值，可能与实际值有偏差
- 误差通常在 ±10% 范围内

**注意事项**:
- 不要依赖精确的 token 计数进行计费
- 用于监控和调试目的即可

### 3. 思考内容 (reasoning_content)

**扩展字段**: 非 OpenAI 标准字段，用于返回 AI Studio 的 "thinking" 过程。

**格式**:
```json
{
  "message": {
    "role": "assistant",
    "content": "最终回答",
    "reasoning_content": "思考过程（如果模型提供）"
  }
}
```

**兼容性**: OpenAI SDK 会忽略未知字段，不影响正常使用。

### 4. 模型切换

**行为**: `model` 参数用于在 AI Studio 页面切换模型。

**限制**:
- 模型切换需要时间（2-5秒），首次请求会较慢
- 模型 ID 必须存在于 `/v1/models` 返回的列表中
- 不支持同时使用多个模型（不支持 `n > 1`）

**建议**:
- 使用 `excluded_models.txt` 过滤不需要的模型
- 连续请求使用相同模型时性能更好

### 5. 函数调用 (Function Calling)

**支持情况**:
- ✅ 支持 Google Search 工具（通过 AI Studio 原生能力）
- ⚠️ 自定义函数需要通过 MCP (Model Context Protocol) 适配器
- ❌ 不支持 OpenAI 的原生函数调用格式的直接透传

**Google Search 示例**:
```json
{
  "tools": [
    {
      "type": "google_search"
    }
  ]
}
```

### 6. 参数控制机制

**三层响应获取机制**对参数支持的影响：

1. **集成流式代理模式** (默认，端口 3120)
   - ✅ 支持基础参数：`model`, `temperature`, `max_tokens`
   - ⚠️ 部分高级参数可能不生效
   - ⚡ 性能最优，延迟最低

2. **外部 Helper 服务模式** (可选配置)
   - 参数支持取决于 Helper 服务实现
   - 需要有效的认证文件

3. **Playwright 页面交互模式** (后备方案)
   - ✅ 完整支持所有参数
   - 通过页面操作设置参数
   - 延迟较高但功能完整

---

## 兼容性测试

### 使用 OpenAI Python SDK

```python
from openai import OpenAI

# 配置代理
client = OpenAI(
    base_url="http://127.0.0.1:2048/v1",
    api_key="your-api-key-or-dummy"  # 如果服务器不需要认证，可以是任意值
)

# 非流式请求
response = client.chat.completions.create(
    model="gemini-2.5-pro",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
print(response.choices[0].message.content)

# 流式请求
stream = client.chat.completions.create(
    model="gemini-2.5-pro",
    messages=[
        {"role": "user", "content": "Tell me a story"}
    ],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### 使用 OpenAI Node.js SDK

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://127.0.0.1:2048/v1',
  apiKey: 'your-api-key-or-dummy',
});

// 非流式
const response = await client.chat.completions.create({
  model: 'gemini-2.5-pro',
  messages: [{ role: 'user', content: 'Hello!' }],
});
console.log(response.choices[0].message.content);

// 流式
const stream = await client.chat.completions.create({
  model: 'gemini-2.5-pro',
  messages: [{ role: 'user', content: 'Tell me a story' }],
  stream: true,
});

for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content || '');
}
```

---

## 已知问题和解决方案

### 问题 1: 流式响应中断

**现象**: SSE 流式响应中途断开连接。

**可能原因**:
1. 浏览器页面错误
2. AI Studio 服务临时不可用
3. 网络代理配置问题
4. 流式代理服务异常

**解决方案**:
1. 检查 `/health` 端点确认服务状态
2. 查看日志文件 `logs/app.log`
3. 尝试禁用流式代理：在 `.env` 中设置 `STREAM_PORT=0`
4. 检查网络和代理配置

### 问题 2: 模型列表为空

**现象**: `/v1/models` 返回空列表或只有默认模型。

**可能原因**:
1. AI Studio 页面未加载完成
2. 认证文件过期
3. 网络拦截失败
4. 页面结构变化

**解决方案**:
1. 等待服务完全启动（查看启动日志）
2. 更新认证文件（使用 `--debug` 模式重新认证）
3. 检查浏览器连接状态
4. 查看 `errors_py/` 目录的错误快照

### 问题 3: 参数不生效

**现象**: 设置的 `temperature`、`max_tokens` 等参数似乎没有效果。

**可能原因**:
1. 使用流式代理模式，参数未透传到 AI Studio
2. AI Studio 页面的 "高级选项" 未展开
3. 特定模型不支持某些参数

**解决方案**:
1. 确认 `localStorage.isAdvancedOpen=true`（启动时自动设置）
2. 查看日志确认参数是否成功设置
3. 尝试禁用流式代理，使用 Playwright 模式
4. 参考 AI Studio 官方文档了解模型限制

---

## 最佳实践

### 1. 客户端配置

**设置合理的超时**:
```python
client = OpenAI(
    base_url="http://127.0.0.1:2048/v1",
    api_key="your-api-key",
    timeout=60.0,  # 秒，根据需要调整
)
```

**处理流式响应异常**:
```python
try:
    stream = client.chat.completions.create(..., stream=True)
    for chunk in stream:
        # 处理分块
        pass
except Exception as e:
    print(f"Stream error: {e}")
    # 实现重试逻辑
```

### 2. 模型选择

**优先使用高性能模型**:
- `gemini-2.5-flash` - 快速响应，适合对话
- `gemini-2.5-pro` - 平衡性能和质量
- `gemini-exp-*` - 实验性模型，功能最新但可能不稳定

**避免频繁切换模型**:
- 模型切换有延迟，影响性能
- 同一会话尽量使用同一模型

### 3. 错误处理

**实现重试机制**:
```python
from openai import OpenAI, APIError
import time

def chat_with_retry(client, messages, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model="gemini-2.5-pro",
                messages=messages
            )
        except APIError as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避
                continue
            raise
```

### 4. 性能优化

**启用流式代理**:
```env
STREAM_PORT=3120  # 默认值，确保启用
```

**合理配置超时**:
```env
RESPONSE_COMPLETION_TIMEOUT=300000  # 5分钟，根据需要调整
SILENCE_TIMEOUT_MS=60000  # 1分钟无输出超时
```

---

## 兼容性路线图

### 近期计划

- [ ] 改进 token 计数精度（使用官方 tokenizer）
- [ ] 支持更多 AI Studio 原生功能
- [ ] 优化流式响应分块策略
- [ ] 改进参数透传机制

### 长期计划

- [ ] 支持图像生成（如 AI Studio 添加此功能）
- [ ] 支持多模态输入（图像、音频）
- [ ] 支持更多 OpenAI API 端点
- [ ] 实现完整的函数调用支持

---

## 相关文档

- [API 使用指南](api-usage.md) - API 端点详细说明
- [流式处理模式详解](streaming-modes.md) - 三层响应获取机制
- [环境变量配置指南](environment-configuration.md) - 配置参数说明
- [故障排除指南](troubleshooting.md) - 常见问题解决

---

**最后更新**: 2024年11月  
**当前版本**: v0.6.0

如有疑问或发现兼容性问题，请提交 Issue 反馈。
