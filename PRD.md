# CyberGrunt (赛博牛马) 2.0：大脑-手脚 (Brain-Limb) 架构重构 PRD

## 1. 项目背景与目标
CyberGrunt 1.0 成功验证了纯 Python 环境下 Agent 闭环的可行性。
然而，随着功能增加，原有的 `tools.py` 膨胀严重，且同步阻塞的交互模式限制了复杂任务的处理能力。
**2.0 目标**：借鉴 OpenClaw 的模块化思想，实现“大脑”与“手脚”的物理隔离，构建一个支持异步指令、动态技能扩展、多设备协同的智能体操作系统。

## 2. 核心概念定义
- **大脑 (The Brain)**：Agent 的中枢神经系统。负责语义理解、任务规划、记忆检索和最终回复生成。
- **手脚 (The Limbs)**：Agent 的执行器官。
    - **Core Limbs**：基础能力（文件、系统、进程）。
    - **Skills**：通过纯 Python 扩展的高阶业务能力。
    - **MCP Limbs**：通过 Model Context Protocol 挂载的外部生态工具。
- **肢体中枢 (Limb Hub)**：负责肢体的发现、注册、版本管理和大脑调用分发。

## 3. 架构设计

### 3.1 逻辑分层图
```text
[ 表现层 / Interface ]
      |-- Web Console (JSON-over-SSE, 异步任务流)
      |-- Telegram / IM (消息防抖, 异步响应)

[ 决策层 / Brain ]
      |-- Brain Central (llm.py: 任务分解, 决策循环)
      |-- Memory System (memory.py: 三层记忆模型)

[ 调度层 / Hub ]
      |-- Limb Hub (hub.py: 动态扫描 limbs/*, 权限校验, 工具注册)

[ 执行层 / Limbs ]
      |-- Core (limbs/core/*: 确定性本地操作)
      |-- Skills (limbs/skills/*: 模块化插件)
      |-- MCP (limbs/mcp/*: 外部协议桥接)
```

### 3.2 关键职责划分
| 组件 | 详细职责 | 物理位置 |
| :--- | :--- | :--- |
| **Main** | 系统入口、HTTP Server、EventBus 事件推送、配置加载 | `/main.py` |
| **Brain** | 封装 LLM 交互逻辑、Session 管理、提示词模板工程 | `/brain/` |
| **Memory** | 语义数据库管理（LanceDB）、会话压缩、事实提取 | `/brain/memory/` |
| **Limb Hub** | 工具发现器。自动解析 `limbs/` 下的函数并转化为 OpenAI Tools 格式 | `/limbs/hub.py` |
| **Limbs** | 按照业务逻辑细分的所有工具实现 | `/limbs/core/`, `/limbs/skills/` |

## 4. 核心功能需求

### 4.1 肢体动态注册 (Limb Auto-Discovery)
- **需求**：Limb Hub 必须在启动时自动扫描 `/limbs` 目录下的所有子目录。
- **规范**：每个 Skill 建议为一个独立文件，通过 `@limb` 装饰器定义其名称、描述和参数 Schema。
- **价值**：开发者新增功能只需在 `/limbs/skills` 下丢入一个 `.py` 文件，无需修改核心代码。

### 4.2 异步任务流 (Asynchronous Tasking)
- **需求**：大脑接收到复杂指令后，应立即反馈“任务已启动”，并在后台持续通过 EventBus 推送肢体执行日志。
- **交互**：前端 Web 界面需支持“多任务并发显示”，用户可以随时取消或干预正在进行的任务。

### 4.3 统一协议标准 (Unified Protocol)
- **内部协议**：大脑与肢体之间采用标准的 JSON-RPC 风格调用。
- **外部协议**：接入层与前端之间采用标准的 JSON-over-SSE，解决所有转义与多行渲染问题。

### 4.4 本地与远程协同 (Local & Remote Nodes)
- **参考 OpenClaw**：支持定义“远程肢体”。大脑可以调用另一台机器上的 `exec` 工具，实现跨机器自动化。

## 5. 目录结构规范
```text
724-office/
├── main.py               # 路由器与事件总线
├── brain/
│   ├── central.py        # 决策循环 (原 llm.py)
│   ├── memory/           # 记忆系统
│   └── prompts/          # 身份与任务模板
├── limbs/
│   ├── hub.py            # 肢体管理中心
│   ├── core/             # 基础肢体 (fs, shell, system)
│   ├── skills/           # 插件技能 (search, video, code_gen)
│   └── mcp/              # MCP 连接器
├── workspace/            # 用户数据与生成文件
├── sessions/             # 会话持久化
└── config.json           # 全局配置
```

## 6. 非功能性需求
- **稳定性**：任何肢体执行崩溃都不应导致大脑或主进程退出。
- **安全性**：涉及 `shell.exec` 的核心肢体必须在配置中显式白名单授权。
- **可移植性**：保持零框架依赖（除了必要的 `lancedb` 等驱动），确保在 Jetson/Mac/Linux 轻松运行。

## 7. 演进路线
1. **Phase 1**: 建立目录结构，迁移 `llm.py` 为 `brain/` 模块，实现 Limb Hub 基础加载。
2. **Phase 2**: 重构 `main.py` 为真正的异步事件驱动架构。
3. **Phase 3**: 迁移现有 26 个工具到 `limbs/` 对应分类下。
4. **Phase 4**: 升级 Web 控制台，支持多任务进度条展示。
