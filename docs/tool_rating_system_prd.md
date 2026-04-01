# 工具质量评分机制 (Tool Rating System) PRD

## 1. 业务目标
为了应对“AI 自动写代码”可能带来的系统劣化问题，必须引入一套量化的工具监控与熔断机制。
当新工具（Limb）被运行时创建后，系统必须监控其生命周期健康度。劣质工具将被自动降级为“实验性”，防止其在无人值守时持续污染业务数据或阻塞进程。

## 2. 核心指标定义
每执行一次工具调用，系统必须在内存或持久化存储中更新以下指标：
- `invoke_count`: 总调用次数。
- `success_count`: 成功调用次数（未抛出异常，且返回值不包含 `[error]`）。
- `avg_duration_ms`: 平均执行耗时。
- `last_used_at`: 最后一次调用的时间戳。

**评分公式 (Health Score)**:
`Score = (success_count / invoke_count) * 100` (基础分)
如果 `avg_duration_ms` > 5000ms，扣 10 分。

## 3. 功能需求

### 3.1 指标采集拦截器 (Metric Interceptor)
- **位置**: `limbs/hub.py` 中的 `execute` 方法。
- **逻辑**: 在执行任何工具前后进行打点计时。拦截异常并正确分类。
- **持久化**: 内存缓存指标，并在适当的时机（或伴随 `self_repair` 的周期）落盘到 `workspace/files/tool_metrics.json`。

### 3.2 动态降级机制 (Degradation)
- 当一个工具的 `invoke_count` > 5，且 `Score` < 60 时，该工具被标记为 **`experimental` (实验性)**。
- 实验性工具在被 LLM 调用时，如果再次失败，应在返回给 LLM 的错误信息中强力警告：“该工具极不稳定，建议重写或弃用”。

### 3.3 新增诊断工具
- **提供给 AI 的新能力**: 增加一个内置工具 `tool_health_report`，无需参数，返回所有自定义工具的当前评分和健康状态排名。

## 4. 测试与验收标准 (IronGate 专属防线)
Dev AI 提交实现前，必须提供以下自动化测试：
1. **L1 单元测试**: `tests/test_tool_metrics.py`，必须覆盖：
   - 成功执行的统计累加。
   - 失败执行（抛出异常或返回 `[error]`）的统计累加。
   - 降级逻辑触发边界（第 6 次调用且成功率低于 60% 时，状态改变）。
2. **性能基线**: 指标采集拦截器对 `hub.execute` 的性能损耗不得超过 2ms。

## 5. 开发建议
请 @Forge 重点修改 `limbs/hub.py`，考虑使用装饰器或上下文管理器来优雅地包裹原有的 `fn(args, ctx)`。
不要立刻去改动 `brain/central.py`，监控的逻辑应该收敛在手脚系统的调度中心 (Hub) 内。
