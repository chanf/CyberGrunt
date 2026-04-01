# GEMINI.md - 7/24 Office Context

# GEMINI.md - CyberGrunt (赛博牛马) Context

This document provides architectural context and development guidelines for the **CyberGrunt** project, a self-evolving AI agent system.

**CyberGrunt** is a production-grade AI agent built in pure Python with zero framework dependencies (no LangChain, LlamaIndex, etc.). It is designed for 24/7 operation, featuring a robust tool-use loop, a three-layer memory system, and the ability to self-repair and evolve by creating its own tools at runtime.


### Core Technologies
- **Language:** Python 3 (standard library focused)
- **External Dependencies:** `croniter`, `lancedb`, `websocket-client`
- **Architecture:** Modular, multi-tenant (Docker-ready), event-driven

## Architecture & Key Files

The system is organized into a few core modules with clear responsibilities:

- `main.py`: **Entry Point**. Starts the HTTP server, handles messaging platform callbacks (e.g., WeChat Work), manages message debouncing, and coordinates between modules.
- `llm.py`: **Core Tool Use Loop**. Manages the iterative process of user message -> LLM -> tool call -> execution -> LLM. Handles session management and 40-message history limits.
- `tools.py`: **Tool Registry**. Contains all LLM-callable tool definitions and their implementations. Features a `@tool` decorator for easy extensibility.
- `memory.py`: **Three-Layer Memory**. 
    1. Session (short-term, JSON)
    2. Compressed (long-term, LLM-distilled facts)
    3. Retrieval (active recall via LanceDB vector search)
- `scheduler.py`: **Task Scheduling**. Supports one-shot and recurring (cron) tasks, persistent across restarts.
- `mcp_client.py`: **MCP Bridge**. A custom implementation of the Model Context Protocol (JSON-RPC) to connect to external tool servers.
- `router.py`: **Multi-Tenant Router**. Manages Docker-based provisioning for per-user agent containers.
- `self_check_tool.py`: **Self-Repair**. Diagnostics and error log analysis for system health.

## Building and Running

### Prerequisites
- Python 3.x
- Docker (if using multi-tenant routing)

### Setup
1. **Configure:**
   ```bash
   cp config.example.json config.json
   # Edit config.json with your API keys (LLM, Messaging, ASR, etc.)
   ```
2. **Install Dependencies:**
   ```bash
   pip install croniter lancedb websocket-client
   ```
3. **Initialize Workspace:**
   ```bash
   mkdir -p workspace/memory workspace/files
   ```

### Execution
```bash
python3 xiaowang.py
```
The agent starts an HTTP server on the port specified in `config.json` (default: 8080).

## Development Conventions

### 1. Adding New Tools
To add a capability, simply define a function in `tools.py` and decorate it:
```python
@tool("tool_name", "Description for LLM", {"param1": {"type": "string"}}, ["param1"])
def my_tool(args, ctx):
    # args: dict from LLM
    # ctx: {"owner_id", "workspace", "session_key"}
    return "Result string"
```
**Mandate:** Do not create new files for single tools; keep implementations in `tools.py` or as plugins.

### 2. Zero-Dependency Mindset
Prioritize the Python standard library. Only add external dependencies if absolutely necessary (like `lancedb` for vector search). Avoid complex frameworks that obscure the execution flow.

### 3. Error Handling & Logging
- Use the `log` object (standard `logging`) for all major operations.
- Tools should return descriptive error strings (e.g., `"[error] file not found"`) rather than raising unhandled exceptions in the core loop.

### 4. Self-Evolution
The agent has a `create_tool` capability. When debugging or extending, consider if the agent can implement the required tool itself via runtime Python code generation.

## Documentation References
- `README.md`: High-level overview and feature list.
- `config.example.json`: Full configuration structure.
- `workspace/SOUL.md`: (Optional) Agent personality and behavior rules.
- `workspace/AGENT.md`: (Optional) Operational procedures.
