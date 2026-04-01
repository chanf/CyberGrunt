# CyberGrunt 开发准则 (Development Guidelines)

本手册旨在确保 CyberGrunt 项目在长期、多 AI 协作开发过程中，保持架构一致性、安全性及代码的高可维护性。

## 1. 核心设计原则 (Core Principles)

- **零框架依赖 (Zero-Framework)**: 严禁引入 LangChain, LlamaIndex, CrewAI 等重型智能体框架。系统必须基于 Python 标准库和指定的轻量级包（`croniter`, `lancedb`, `websocket-client`）构建。
- **显式优于隐式 (Explicit over Implicit)**: 代码逻辑必须清晰可见，避免过度使用复杂的元编程或隐藏的抽象层。
- **模块化手脚 (Modular Limbs)**: 工具（Tools）即插件（Limbs）。每个工具都应是独立的函数，通过装饰器注册，互不干扰。

## 2. 目录结构规范 (Directory Structure)

- `main.py`: 唯一的 HTTP 入口与异步任务调度器。
- `brain/`: 存放“决策”逻辑（LLM 调用、上下文管理、长期记忆调度）。
- `limbs/`: 存放“执行”逻辑。
    - `limbs/core/`: 基础系统操作（文件、执行命令）。
    - `limbs/skills/`: 扩展业务能力（搜索、通知、多媒体）。
- `tests/`: 必须为每个核心模块和 Limb 提供单元测试。
- `workspace/`: 智能体唯一的合法活动区域。

## 3. 开发流程 (Development Workflow)

1. **先测试后实现**: 新增功能前，应先在 `tests/` 中定义预期行为。
2. **工具注册**: 必须使用 `from limbs.hub import limb` 装饰器。禁止修改 `brain/central.py` 来添加硬编码工具。
3. **安全性校验**: 
    - 涉及文件操作必须调用 `_resolve_path` 确保路径不脱离 `workspace`。
    - 涉及 `exec` 的工具必须严格限制超时和权限。
4. **错误处理**: 工具必须返回字符串格式的错误信息（例如 `[error] reason`），严禁抛出未捕获的异常中断主循环。

## 4. 代码风格与质量 (Code Style)

- **类型提示**: 核心接口建议使用 Python Type Hints。
- **日志规范**: 使用全局 `log` 对象。关键路径（工具启动、结果返回、LLM 思考）必须有清晰的日志。
- **文档字符串**: 每个 Limb 函数必须包含清晰的描述，因为这些描述会直接作为 Prompt 喂给 LLM。

## 5. 长期进化目标 (Evolution Goal)

- **自修复**: 系统应具备通过 `self_check` 和 `diagnose` 发现并尝试修复自身配置错误的能力。
- **高可用**: 保证 24/7 运行，连接断开后应能自动重连（如 MCP、SSE、数据库）。

## 6. 测试规范 (Testing Protocol)

为了确保项目“基础牢固”，所有开发必须遵循 **[TESTING_STANDARD.md](TESTING_STANDARD.md)** 中定义的三个层级（L1/L2/L3）测试流程。

### 6.1 强制性要求
- **单元测试 (L1)**: 每一个新增的 Limb 函数必须提供独立的单元测试。
- **集成测试 (L2)**: 修改涉及 Brain 决策闭环时，必须通过 `tests/test_integration.py`。
- **E2E 测试 (L3)**: 修改涉及前端交互或 IME 时，必须通过 Playwright 验证。


### 6.2 存储与运行
- **脚本位置**: 所有测试代码必须存放在 `tests/` 目录下，命名格式为 `test_<module_name>.py`。
- **执行方式**: 使用标准库 `unittest`。执行命令参考：`./venv/bin/python -m unittest discover tests`。
- **依赖模拟**: 测试应尽可能使用 `unittest.mock` 模拟外部 API 调用（如 OpenAI/Claude API）及硬件交互，确保测试在离线环境下也可运行。

### 6.3 测试报告 (Test Reports)
- **报告生成**: 在执行大规模更新后，应将测试输出重定向至 `test_reports/` 目录（例如 `python -m unittest ... > test_reports/report_$(date).txt`）。
- **质量准则**: 
    - 单元测试覆盖率应覆盖所有核心分支逻辑。
    - 所有已知漏洞（如路径穿越）必须有对应的防回归测试。
    - 在提交 Git 之前，必须确保所有已有测试全部通过（PASS）。

## 7. 开发与验收流程 (Standard Workflow)

每一个独立的功能模块或 Limb 开发完成后，必须严格执行以下五个步骤：

1.  **单元测试 (Unit Testing)**: 编写并运行针对该功能的专项测试脚本（`tests/test_<name>.py`）。
2.  **回归测试 (Regression Testing)**: 执行全量测试用例（`python -m unittest discover tests`），确保新代码未影响存量功能。
3.  **项目验收 (User Acceptance)**: 提供清晰的操作指令或场景，供用户手动验证功能是否符合预期。
4.  **文档同步 (Documentation)**: 修改 `README.md` 及相关文档，更新特性列表、架构说明或工具手册。
5.  **规范提交 (Git Commit)**: 将所有改动提交至仓库，并使用**中文**详细说明本次修改的内容及影响。

**注意：** AI 必须在每完成一个功能周期的上述五个步骤后停止，并请求用户验收。严禁在未获得用户明确指令（如“验收通过”）的情况下擅自开启下一阶段的开发。

---

*注：本文件由 Gemini CLI 生成，作为后续所有 AI 参与开发的最高指令参考。*
