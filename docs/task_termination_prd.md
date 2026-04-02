# 任务终止与交互式确认 (Task Termination) PRD

## 1. 业务背景
目前 Agent 一旦启动执行（最多循环 20 次），人类无法在中途干预。如果 Agent 陷入死循环或生成了错误代码，必须等待其超时，这不仅浪费 Token，还可能产生副作用。

## 2. 核心功能需求

### 2.1 物理终止接口 (Hard Kill)
- **后端接口**: `POST /api/task/stop`
- **逻辑**: 
  - 系统必须维护一个 `active_tasks` 的映射表（session_id -> thread/process object）。
  - 当收到 stop 请求时，通过 `ctypes` 强行抛出异常或利用 `threading.Event` 标记位中断当前的 `llm.chat` 循环。
- **SSE 通知**: 终止成功后，必须向客户端推送一个 `type: "lifecycle", phase: "aborted"` 的事件。

### 2.2 前端控制逻辑
- **UI 元素**: 
  - 在 `sendBtn` 旁边增加一个 `stopBtn` (默认隐藏)。
  - 当任务进入 `Thought` 或 `Tool Call` 阶段时显示该按钮。
- **TestID**: 必须包含 `data-testid="stop-button"`。

## 3. 安全要求
- 只有任务的启动者或 `OWNER_IDS` 列表中的用户才有权终止任务。
- 终止动作必须被 `QA-Sniffer` 捕捉并记入审计日志。

## 4. 验收标准 (IronGate 准则)
1. **L3 E2E 测试**: 使用 Playwright 点击 Stop 按钮，验证 `system-status-bar` 立即变回“Ready”状态。
2. **内存泄露检查**: 强行终止 10 次任务后，系统内存占用不得有显著波动。
