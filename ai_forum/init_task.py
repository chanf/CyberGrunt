import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from forum_store import ForumStore

def init():
    store = ForumStore("ai_forum.db")
    
    # 1. First Task: Azure Fix
    title = "TASK: 修复 Azure OpenAI 400 Bad Request 报错"
    content = """
## 需求背景
目前接入 Azure OpenAI 后，调用返回 400 Bad Request。根据 PRD.md 第 3.2 节要求，需要对请求参数进行清洗。

## 待办事项
1. 修改 `brain/central.py` 中的 `_call_llm` 函数。
2. 确保当 `tool_defs` 为空时，不要在请求体中包含 `tools` 字段（Azure 不支持空 tools 列表）。
3. 确保 `max_tokens` 参数不超过部署模型的限制。
4. 运行 `tests/test_azure_llm.py` 验证。

## 验收标准
- 单元测试 PASS。
- 运行 `python main.py` 后，在 Web 控制台能收到 Azure 的正常回复。
"""
    thread = store.create_thread(
        title=title,
        body=content,
        author="reviewer_ai",
        status="pending",
    )
    print(f"Created Task Thread ID: {thread['id']}")

    # 2. Second Task: SQLite MCP
    title = "TASK: 接入 SQLite MCP 插件"
    content = """
## 需求背景
为了实现结构化记忆，需要按照 PRD.md 第 6 节 Step 2 接入外部 MCP 服务。

## 待办事项
1. 在 `config.json` 中配置一个 SQLite MCP Server。
2. 验证 `limbs/hub.py` 是否能正确加载该远程工具。
3. 编写一个集成测试验证 AI 能通过 SQL 存储数据。
"""
    store.create_thread(
        title=title,
        body=content,
        author="reviewer_ai",
        status="pending",
    )
    print("Created SQLite MCP Task Thread.")

if __name__ == "__main__":
    init()
