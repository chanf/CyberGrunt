# OpenClaw 24/7 长时间自持运行原理解析

在深度的源码与架构文档（尤其是 `agent-loop.md`）分析后，我发现了 OpenClaw 能够实现真正的“无人值守、长时间自持运行”的四大秘密武器。这些机制是区分“玩具脚本”和“工业级 Agent 操作系统”的分水岭。

## 秘密一：单线序列化队列 (Session Lane Serialization)
AI 最大的崩溃源之一是**状态竞态 (Race Conditions)**。当用户在不同设备上同时发消息，或者一个定时任务与用户聊天发生碰撞时，如果同时修改记忆，系统会立刻崩溃。
- **OpenClaw 的做法**: 引入了严格的 `per-session queue` (会话级队列) 加上可选的全局队列。
- **原理**: 它强制将所有意图（无论来自 Web、Telegram 还是 Cron）排入单线车道。一个 Session 在同一时刻只允许一个 Agent Loop 存活。这不仅避免了竞态，还确保了 `SessionManager` 写锁的绝对安全。

## 秘密二：极其强悍的生命周期拦截器 (Hook Pipeline)
长时间运行意味着环境在不断变化（Token 快满了、网络断了、工具报错了）。
- **OpenClaw 的做法**: 它的 Loop 不是硬编码的黑盒，而是一个布满“探针”的流水线。
- **原理解析**:
  - `before_prompt_build`: 在发起请求前，动态决定切除哪些旧记忆。
  - `before_tool_call` / `after_tool_call`: 工具执行失败时，拦截器可以直接 `block: true` 并就地执行恢复逻辑，而不需要抛出异常让整个 Agent 崩溃。
  - **启发**: 错误在局部被消化，这是长治久安的核心。

## 秘密三：自动记忆压实与重试免疫 (Compaction & Retries)
长时间对话会导致上下文无限膨胀，最终触碰 LLM 的 Token 上限（如 128k），导致 400 错误。
- **OpenClaw 的做法**: 自动记忆压实 (Auto-Compaction)。
- **原理解析**:
  - 当 Token 逼近危险水位时，触发 `compaction` 事件。系统会暂停当前推理，调用一个廉价的小模型（或专门的 Prompt）将前 50 轮对话压缩为“核心事实快照”。
  - 更关键的是 **“重试免疫”**: 在压缩或重试期间，它会清空内存中的流式 Buffer 和工具调用残影，防止在重试成功后，给用户发送两遍一样的消息。

## 秘密四：网关与执行器的物理熔断 (Timeout & Abort)
死循环是自主 Agent 最大的噩梦（例如：工具报错 -> 告诉 LLM -> LLM 再调用报错工具）。
- **OpenClaw 的做法**: 极端的超时与退出机制。
- **原理解析**:
  - **默认 48 小时硬熔断**: Agent runtime 层强制设定了 `timeoutSeconds` (默认 172800s)。一旦超过，底层的 `AbortSignal` 会直接切断所有 IO 和计算资源。
  - **RPC 层剥离**: `agent.wait` 默认只等 30 秒。如果 Agent 在深思熟虑，网关不会被它拖死，而是先返回状态，让前端知道“它还在想”，前端可以通过事件流 (SSE) 慢慢收日志。

---

## 给 CyberGrunt 的下一步演进建议
我们目前的 CyberGrunt 已经通过 `tests/` 防线实现了“静态代码”的安全，但**运行时 (Runtime) 的免疫力**还不够。

**IronGate 的 QA 建议**：
1. **移植 Queue 机制**：必须重写 `brain/central.py`，禁止同一个 `session_id` 被两个 HTTP 请求并发重入。
2. **落地 Compaction**：当 `max_tokens` 逼近时，强制截断并压缩历史，这是保证 Azure OpenAI 永远不再报 400 的终极方案。
