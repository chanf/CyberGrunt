# CyberGrunt 自动化测试标准规范 (Testing Standard)

本规范定义了 CyberGrunt 项目的质量门禁。所有 Dev AI 提交的代码必须通过本规范定义的测试层级，方可进入验收环节。

## 1. 测试层级架构 (Testing Pyramid)

### L1: 单元测试 (Unit Tests)
- **目标**: 验证单个函数、单个 Limb 的逻辑。
- **存储**: `tests/test_<limb_name>.py` 或 `tests/test_<module_name>.py`。
- **强制要求**:
    - **禁止真实网络请求**: 必须使用 `unittest.mock` 模拟外部 API。
    - **路径沙箱验证**: 涉及文件的 Limb 必须包含路径穿越 (Path Traversal) 测试。
- **运行命令**: `./venv/bin/python -m unittest tests/test_<name>.py`

### L2: 集成测试 (Integration Tests)
- **目标**: 验证“用户 -> 大脑 -> 工具调用 -> 回复”的闭环。
- **核心文件**: `tests/test_integration.py`。
- **强制要求**:
    - 模拟 LLM 返回的 JSON 响应。
    - 验证 `workspace/` 下是否产生了预期的物理变更（如文件被创建）。
    - 验证 `Registry` 注册表状态。

### L3: E2E 测试 (End-to-End Tests)
- **目标**: 从用户操作浏览器的视角验证系统。
- **技术栈**: Playwright (Python)。
- **核心文件**: `tests/test_e2e_web.py`。
- **强制要求**:
    - 必须涵盖：页面加载、消息发送、日志流显示、中文字符输入 (IME)。
    - **执行环境**: 使用 `tests/e2e_config.json` 在 8081 端口运行隔离实例。

## 2. 开发者的强制义务 (Dev Requirements)

1.  **新增 Limb**: 必须同步提交对应的 L1 单元测试脚本。
2.  **修改 Brain**: 必须运行并确保 L2 集成测试全部 PASS。
3.  **UI 变更**: 必须更新 L3 E2E 脚本，捕获新的 DOM 元素或验证交互。
4.  **漏洞修复**: 必须增加一个“失败用例”来复现 Bug，修复后再确保该用例 PASS（防回归）。

## 3. 验收合格标准 (Definition of Ready)

一个功能被视为“可交付验收”必须满足：
- [ ] L1 + L2 + L3 测试通过率 **100%**。
- [ ] 运行 `./venv/bin/python -m unittest discover tests` 无任何 Error 或 Failure。
- [ ] 测试报告已重定向至 `test_reports/`。
- [ ] 在 `ai_forum` 中发布 `PR_READY` 贴，并附带测试运行截图/日志。

## 4. 质量工具链

- **Runner**: `unittest` (Python 标准库)
- **Mocking**: `unittest.mock`
- **Browser Automation**: `Playwright`
- **Static Analysis**: `flake8` / `mypy` (后续引入)

---
*QA 实例提醒：我会盯着 `tests/` 目录，任何没有测试的 PR 都会被回帖拒绝。*
