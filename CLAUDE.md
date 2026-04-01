# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CyberGrunt (赛博牛马) is a self-evolving AI agent system built with ~3,500 lines of pure Python. It has **zero framework dependencies** (no LangChain, LlamaIndex, or CrewAI) and runs 24/7 in production.

**Architecture:** 入口-大脑-手脚 (Gateway → Brain → Limbs)
- `main.py`: HTTP server, EventBus (SSE), async task dispatch
- `brain/central.py`: LLM tool-use loop (max 20 iterations), session management, multimodal handling
- `limbs/`: Modular tools with hot-reloading
  - `limbs/core/`: File operations, command execution
  - `limbs/skills/`: Search, media, memory, notifications
  - `limbs/hub.py`: Tool registry and dispatcher
- `ai_forum/`: Standalone collaboration server for AI-to-A communication (threads, replies, status)

## Common Commands

### Running the Agent
```bash
python3 main.py  # Starts HTTP server on port 8088 (default)
```

### Running Tests (Three-Level Pyramid)
```bash
# L1: Unit tests
./venv/bin/python -m unittest tests/test_<limb_name>.py

# L2: Integration tests
./venv/bin/python -m unittest tests/test_integration.py

# L3: E2E tests (requires Playwright)
./venv/bin/python tests/test_e2e_web.py

# Run all tests
./venv/bin/python -m unittest discover tests
```

### AI Forum Server
```bash
# Forum runs on port 8090
# API examples:
curl -X POST http://localhost:8090/api/threads -H 'Content-Type: application/json' \
  -d '{"author": "developer_ai", "title": "[测试报告] ...", "body": "...", "status": "pending"}'
curl 'http://localhost:8090/api/actionable?author=reviewer_ai'
curl http://localhost:8090/api/events  # SSE stream
```

## Core Design Principles

1. **Zero Framework**: Only standard library + 3 lightweight packages (`croniter`, `lancedb`, `websocket-client`)
2. **Explicit over Implicit**: No magic, all logic visible and debuggable
3. **Modular Limbs**: Tools = plugins. Use `@limb` decorator from `limbs.hub` to register
4. **Path Sandboxing**: All file ops must use `_resolve_path` to prevent escaping `workspace/`
5. **Hot-Reloading**: Monitor `plugins/` directory, load with `exec()` + independent namespace

## Adding New Tools (Limbs)

```python
# In limbs/core/ or limbs/skills/
from limbs.hub import limb

@limb(
    name="your_tool_name",
    description="Clear description (LLM reads this)",
    properties={
        "param1": {"type": "string", "description": "..."}
    },
    required=["param1"]
)
def your_tool_name(args, ctx):
    # args: dict of parameters
    # ctx: session context (sid, owner_id)
    return "result string"  # or "[error] reason"
```

## Testing Requirements (MANDATORY)

Per `TESTING_STANDARD.md` and `AGENT.md`:

- **L1 (Unit)**: Every new Limb must have `tests/test_<name>.py` with mocked external APIs
- **L2 (Integration)**: Changes to Brain require passing `tests/test_integration.py`
- **L3 (E2E)**: UI/IME changes require Playwright validation via `tests/test_e2e_web.py`

**All tests must PASS before git commit.** Save reports to `test_reports/` on large updates.

## Important Paths

| Path | Purpose |
|------|---------|
| `config.json` | API keys, model config (copy from `config.example.json`) |
| `workspace/` | Only legal area for agent file operations |
| `sessions/` | Session history (JSON, last 40 messages per session) |
| `memory_db/` | Long-term vector storage (LanceDB) |
| `plugins/` | Hot-reloadable custom tools |
| `ai_forum/ai_forum.db` | Forum persistence (SQLite) |

## LLM Provider Support

Supports standard OpenAI API and **Azure OpenAI Service**:
- Azure: Set `type: "azure"`, `deployment_name`, `api_version` in config
- Azure endpoints: `{api_base}/openai/deployments/{deployment_name}/chat/completions?api-version={version}`
- Azure auth: Uses `api-key` header (not `Authorization: Bearer`)
- Request filtering: Azure rejects empty `tools` array; body is sanitized automatically

## Development Workflow (Per AGENT.md)

For each feature:
1. Write unit test (L1)
2. Implement the Limb/feature
3. Run regression tests (`python -m unittest discover tests`)
4. Update documentation (README.md, etc.)
5. Git commit with **Chinese** message explaining changes

**Stop after each feature cycle and await user approval before proceeding.**
