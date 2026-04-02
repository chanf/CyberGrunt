# CyberGrunt (赛博牛马) 2.0 全面设计规约 (PRD & System Design)

## 1. 产品愿景
构建一个**物理隔离（大脑与手脚分离）**、**自进化（运行时工具生成）**、**高可靠（全量自动化测试覆盖）**的 Agent 操作系统。它不依赖于任何复杂的重型框架，仅通过标准协议（JSON-RPC, SSE, HTTP）实现跨平台自动化。

## 2. 角色分工协议
- **PRD/QA AI (本实例)**: 负责需求定义、测试用例编写 (tests/)、路径安全审计及最终 E2E 验证。
- **Dev AI (执行实例)**: 负责按照 PRD 与单元测试要求，实现 `brain/`、`limbs/` 及 `main.py` 的具体代码。

## 3. 核心功能矩阵 (Feature Matrix)

### 3.1 接入层 (Interface)
- **Web 控制台**: 
    - [x] 支持 Markdown 渲染。
    - [x] **IME 优化**: 必须通过 `composition` 事件拦截中文输入法组词期间的 Enter 发送。
    - [ ] **日志实时流**: 通过 SSE (/events) 推送 Agent 的思考过程 (Thought) 和工具执行状态。
- **消息网关 (Messaging)**:
    - [x] 支持 Telegram / 默认 HTTP 网关。
    - [x] 支持富文本卡片 (Link Card) 与图片发送。

### 3.2 决策层 (The Brain)
- **多模型支持**:
    - [x] **OpenAI 标准**: 支持兼容 API。
    - [x] **Azure OpenAI**: 
        - 必须支持 `api-key` header 鉴权。
        - 必须根据 `deployment_name` 自动构造 Endpoint 路径。
        - 必须具备请求体过滤功能（如：Azure 不接受空 `tools` 列表）。
- **任务调度**: 
    - 单次任务最长支持 20 次工具迭代循环。
    - 任何 LLM 请求失败必须记录完整 Request Body 以供诊断。

### 3.3 执行层 (The Limbs & MCP)
- **模块化 Limbs**: 
    - `limbs/core/`: 存放文件读写 (fs)、系统执行 (exec)。
    - `limbs/skills/`: 存放业务插件（搜索、媒体、内存检索）。
- **插件热加载**:
    - 系统必须监控 `plugins/` 目录。
    - 必须使用 `exec()` + 独立 `namespace` 方式加载，以绕过 Python 模块缓存实现真正热重载。
- **MCP (Model Context Protocol)**:
    - 支持 stdio 传输协议。
    - **容错处理**: 必须能捕获并重连 stdout EOF 错误。

## 4. 关键交互流程 (Sequences)

### 4.1 工具创建与即时生效
1. 用户要求新建工具 -> `create_tool` Limb 被调用。
2. 代码存入 `plugins/name.py`。
3. `hub.py` 触发增量扫描，清空该模块的加载记录并执行 `exec()`。
4. 下一次 LLM 循环即可感知新工具定义。

### 4.2 路径安全性 (Sandbox)
所有涉及文件系统的工具必须调用 `_resolve_path`：
- 严禁使用 `../` 逃逸 `workspace/` 目录。
- 必须校验 `os.path.realpath` 之后的前缀一致性。

## 5. 质量保证体系 (QA Protocol)

### 5.1 自动化测试层级
1. **Limb Unit Tests**: 针对每个独立工具的输入输出测试。
2. **Integration Tests**: 模拟 `_call_llm` 的 Mock 响应，验证大脑决策-执行-反馈闭环。
3. **E2E Tests (Playwright)**: 
    - 启动 `main.py` 真实服务。
    - 使用 Playwright 驱动浏览器模拟用户输入。
    - 验证 DOM 中是否出现了预期的 Bot 回复和日志卡片。

### 5.2 报错诊断规范
系统日志必须包含以下标识符：
- `[llm]`: 大模型请求与返回详情。
- `[limb]`: 工具执行参数与物理返回。
- `[hub]`: 插件扫描与重载日志。

## 6. 演进路线
- **Step 1**: 解决 Azure OpenAI 400 错误（当前重点：参数清洗）。
- **Step 2**: 接入 SQLite MCP 实现结构化记忆。
- **Step 3**: [当前进行中] 38个工具全量合规整改与“大模型容错”逻辑实现 (ReliableLLM)。
- **Step 4**: 实现 Web 界面的任务终止与交互式参数确认。
