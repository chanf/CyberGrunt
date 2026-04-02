"""
LLM Calls + Tool Use Loop + Session Management

Core loop: user message -> LLM -> tool calls -> execute -> LLM -> ... -> final reply
Supports multimodal: images via image_url (base64) to LLM.
"""

import base64
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple

from limbs import hub as limbs_hub
from brain import tool_quality as tool_quality_mod

log = logging.getLogger("agent")
CST = timezone(timedelta(hours=8))

# ============================================================
#  Initialization (injected by main.py)
# ============================================================

_config = {}       # models config
_workspace = ""
_owner_id = ""
_sessions_dir = ""
MAX_SESSION_MESSAGES = 40


def init(models_config, workspace, owner_id, sessions_dir):
    global _config, _workspace, _owner_id, _sessions_dir
    _config = models_config
    _workspace = workspace
    _owner_id = owner_id
    _sessions_dir = sessions_dir
    tool_quality_mod.init(workspace)


# ============================================================
#  LLM API Call
# ============================================================

_RETRYABLE_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _sanitize_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    if ivalue < minimum:
        return default
    return ivalue


def _sanitize_float(value: Any, default: float, minimum: float = 0.0) -> float:
    try:
        fvalue = float(value)
    except (TypeError, ValueError):
        return default
    if fvalue < minimum:
        return default
    return fvalue


def _resolve_provider_chain() -> List[Tuple[str, Dict[str, Any]]]:
    providers = _config.get("providers", {})
    if isinstance(providers, dict) and providers:
        ordered_names: List[str] = []
        default_name = _config.get("default")
        if isinstance(default_name, str) and default_name in providers:
            ordered_names.append(default_name)
        else:
            ordered_names.append(next(iter(providers)))

        primary_provider = providers[ordered_names[0]]
        failover_sources = [
            _config.get("failover"),
            _config.get("fallback"),
            primary_provider.get("failover_providers"),
            primary_provider.get("fallback_providers"),
        ]
        for source in failover_sources:
            if not isinstance(source, list):
                continue
            for provider_name in source:
                if isinstance(provider_name, str) and provider_name in providers and provider_name not in ordered_names:
                    ordered_names.append(provider_name)

        for provider_name in providers:
            if provider_name not in ordered_names:
                ordered_names.append(provider_name)

        return [(name, providers[name]) for name in ordered_names]

    # Backward compatibility for tests / legacy configs:
    # {"default": { ... provider config ... }}
    legacy_default = _config.get("default")
    if isinstance(legacy_default, dict):
        return [("default", legacy_default)]

    raise ValueError("Invalid models config: expected providers map or legacy default provider dict")


def _get_provider():
    return _resolve_provider_chain()[0][1]


def _build_request_payload(provider: Dict[str, Any], messages: List[Dict[str, Any]], tool_defs: List[Dict[str, Any]]) -> Tuple[str, Dict[str, str], Dict[str, Any], int]:
    if provider.get("type") == "azure":
        # Azure format: {endpoint}/openai/deployments/{deployment}/chat/completions?api-version={version}
        endpoint = str(provider["api_base"]).rstrip("/")
        deployment = provider["deployment_name"]
        version = provider.get("api_version", "2024-05-01-preview")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={version}"

        headers = {
            "Content-Type": "application/json",
            "api-key": provider["api_key"],
        }
    else:
        # Standard OpenAI-compatible format
        url = str(provider["api_base"]).rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider['api_key']}",
        }

    body: Dict[str, Any] = {"messages": messages}
    if tool_defs:
        body["tools"] = tool_defs

    # Default/cap max_tokens to improve compatibility across Azure deployments.
    configured_max_tokens = _sanitize_int(provider.get("max_tokens", 4000), 4000, minimum=1)
    body["max_tokens"] = min(configured_max_tokens, 4000)

    # Standard OpenAI requires 'model', but Azure embeds deployment in URL.
    if provider.get("type") != "azure":
        body["model"] = provider.get("model", "gpt-3.5-turbo")

    extra = provider.get("extra_body", {})
    if isinstance(extra, dict):
        body.update(extra)

    timeout = _sanitize_int(provider.get("timeout", 120), 120, minimum=1)
    return url, headers, body, timeout


def _retry_config(provider: Dict[str, Any]) -> Tuple[int, float, float, float]:
    global_retry = _config.get("retry", {})
    if not isinstance(global_retry, dict):
        global_retry = {}
    provider_retry = provider.get("retry", {})
    if not isinstance(provider_retry, dict):
        provider_retry = {}

    max_attempts = _sanitize_int(
        provider_retry.get(
            "max_attempts",
            provider.get(
                "max_attempts",
                global_retry.get("max_attempts", 2),
            ),
        ),
        2,
        minimum=1,
    )
    base_delay = _sanitize_float(
        provider_retry.get(
            "base_delay_sec",
            provider.get(
                "retry_base_delay_sec",
                global_retry.get("base_delay_sec", 0.8),
            ),
        ),
        0.8,
        minimum=0.0,
    )
    max_delay = _sanitize_float(
        provider_retry.get(
            "max_delay_sec",
            provider.get(
                "retry_max_delay_sec",
                global_retry.get("max_delay_sec", 8.0),
            ),
        ),
        8.0,
        minimum=base_delay if base_delay > 0 else 0.1,
    )
    jitter = _sanitize_float(
        provider_retry.get(
            "jitter_sec",
            provider.get(
                "retry_jitter_sec",
                global_retry.get("jitter_sec", 0.2),
            ),
        ),
        0.2,
        minimum=0.0,
    )
    if max_delay < base_delay:
        max_delay = base_delay
    return max_attempts, base_delay, max_delay, jitter


def _compute_backoff(attempt: int, base_delay: float, max_delay: float, jitter: float) -> float:
    delay = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    if jitter > 0:
        delay += random.uniform(0.0, jitter)
    return max(0.0, delay)


def _http_error_text(err: urllib.error.HTTPError) -> str:
    try:
        return err.read().decode("utf-8", errors="replace")[:1000]
    except Exception:
        return ""


def _call_llm(messages, tool_defs):
    provider_chain = _resolve_provider_chain()
    last_error: Exception | None = None

    for idx, (provider_name, provider) in enumerate(provider_chain):
        max_attempts, base_delay, max_delay, jitter = _retry_config(provider)

        for attempt in range(1, max_attempts + 1):
            url, headers, body, timeout = _build_request_payload(provider, messages, tool_defs)
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                body_text = _http_error_text(e)
                log.error("[llm] provider=%s attempt=%s/%s HTTP %s: %s", provider_name, attempt, max_attempts, e.code, body_text)
                log.error("[llm] Request URL: %s", url)
                log.error("[llm] Request Body: %s", json.dumps(body, ensure_ascii=False)[:2000])
                last_error = e

                should_retry = e.code in _RETRYABLE_HTTP_CODES and attempt < max_attempts
                if should_retry:
                    delay = _compute_backoff(attempt, base_delay, max_delay, jitter)
                    log.warning("[llm] retry provider=%s in %.2fs (HTTP %s)", provider_name, delay, e.code)
                    time.sleep(delay)
                    continue
                break
            except urllib.error.URLError as e:
                log.error("[llm] provider=%s attempt=%s/%s URLError: %s", provider_name, attempt, max_attempts, e)
                last_error = e
                if attempt < max_attempts:
                    delay = _compute_backoff(attempt, base_delay, max_delay, jitter)
                    log.warning("[llm] retry provider=%s in %.2fs (network)", provider_name, delay)
                    time.sleep(delay)
                    continue
                break
            except TimeoutError as e:
                log.error("[llm] provider=%s attempt=%s/%s timeout: %s", provider_name, attempt, max_attempts, e)
                last_error = e
                if attempt < max_attempts:
                    delay = _compute_backoff(attempt, base_delay, max_delay, jitter)
                    log.warning("[llm] retry provider=%s in %.2fs (timeout)", provider_name, delay)
                    time.sleep(delay)
                    continue
                break

        if idx < len(provider_chain) - 1:
            next_name = provider_chain[idx + 1][0]
            log.warning("[llm] failover from provider '%s' to '%s'", provider_name, next_name)

    if last_error:
        raise last_error
    raise RuntimeError("LLM call failed without explicit exception")


# ============================================================
#  Session Management
# ============================================================

def _session_path(session_key):
    safe = session_key.replace("/", "_").replace(":", "_").replace("\\", "_")
    return os.path.join(_sessions_dir, f"{safe}.json")


def _load_session(session_key):
    path = _session_path(session_key)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                messages = json.load(f)
            if len(messages) > MAX_SESSION_MESSAGES:
                evicted = messages[:-MAX_SESSION_MESSAGES]
                messages = messages[-MAX_SESSION_MESSAGES:]
                # Compress evicted messages into long-term memory
                try:
                    from brain import memory as mem_mod
                    mem_mod.compress_async(evicted, session_key)
                except Exception as e:
                    log.error("[session] load-time compress error: %s" % e)
            # Truncation may leave orphan tool messages at the start (no matching
            # assistant + tool_calls), or assistant with tool_calls but truncated
            # tool results. Some LLMs require valid message sequences or return 400.
            # Skip to first user message.
            while messages and messages[0].get("role") not in ("user", "system"):
                messages.pop(0)
            return messages
        except Exception:
            return []
    return []


def _strip_images_for_storage(messages):
    """Before saving session, replace image_url in multimodal content with [image] text.

    Reason: some LLMs don't accept image_url format in history messages (400 error).
    Images only need to be sent to LLM in current turn; text markers suffice for history.
    """
    cleaned = []
    for msg in messages:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            # Multimodal content -> extract text, replace images with markers
            text_parts = []
            for item in msg["content"]:
                if item.get("type") == "text":
                    text_parts.append(item["text"])
                elif item.get("type") == "image_url":
                    text_parts.append("[image]")
            cleaned.append({"role": "user", "content": "\n".join(text_parts)})
        else:
            cleaned.append(msg)
    return cleaned


def _save_session(session_key, messages):
    if len(messages) > MAX_SESSION_MESSAGES:
        evicted = messages[:-MAX_SESSION_MESSAGES]
        messages = messages[-MAX_SESSION_MESSAGES:]
        # Hook 2: async compress evicted messages into long-term memory
        try:
            from brain import memory as mem_mod
            mem_mod.compress_async(evicted, session_key)
        except Exception as e:
            log.error("[session] memory compress error: %s" % e)
    messages = _strip_images_for_storage(messages)
    path = _session_path(session_key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=None)
    except Exception as e:
        log.error(f"[session] save error: {e}")


def _serialize_assistant_msg(msg_data):
    """Serialize assistant message. Preserve reasoning_content for compatible LLMs."""
    result = {"role": "assistant"}
    result["content"] = msg_data.get("content") or None

    reasoning = msg_data.get("reasoning_content")
    if reasoning:
        result["reasoning_content"] = reasoning

    tool_calls = msg_data.get("tool_calls")
    if tool_calls:
        if "reasoning_content" not in result:
            result["reasoning_content"] = "ok"
        result["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            for tc in tool_calls
        ]
    return result


# ============================================================
#  Multimodal Message Building
# ============================================================

def _image_to_base64_url(image_path):
    """Read image file, return data URI"""
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
    mime = mime_map.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _build_user_message(text, images=None):
    """Build user message, supports plain text or multimodal (text + images)"""
    if not images:
        return {"role": "user", "content": text}

    content = []
    if text:
        content.append({"type": "text", "text": text})
    for img_path in images:
        if os.path.exists(img_path):
            try:
                data_url = _image_to_base64_url(img_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": data_url}
                })
            except Exception as e:
                log.error(f"[vision] failed to encode {img_path}: {e}")
                content.append({"type": "text", "text": f"[image load failed: {img_path}]"})
    return {"role": "user", "content": content}


# ============================================================
#  System Prompt
# ============================================================


def _get_recent_scheduler_context():
    """Read recent scheduler session output for cross-session context bridging.

    Scheduled tasks (e.g. self-check reports) send messages to the user via
    the scheduler session, but user replies go through the DM session.
    This function extracts recent (2h) scheduler output and injects it
    into the system prompt so the agent knows what it just sent.
    """
    sched_path = _session_path("scheduler")
    if not os.path.exists(sched_path):
        return ""

    # Freshness check: skip if file modified more than 2 hours ago
    mtime = os.path.getmtime(sched_path)
    now_ts = datetime.now(CST).timestamp()
    if now_ts - mtime > 7200:  # 2 hours
        return ""

    try:
        with open(sched_path, "r", encoding="utf-8") as f:
            msgs = json.load(f)
    except Exception:
        return ""

    if not msgs:
        return ""

    # Find the last message tool call content (what was actually sent to user)
    sent_content = None
    for msg in reversed(msgs):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("function", {}).get("name") == "message":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        sent_content = args.get("content", "")
                    except (json.JSONDecodeError, KeyError):
                        pass
                    if sent_content:
                        break
        if sent_content:
            break

    if not sent_content:
        return ""

    # Truncate overly long content
    if len(sent_content) > 800:
        sent_content = sent_content[:800] + "\n...(truncated)"

    from_time = datetime.fromtimestamp(mtime, CST).strftime("%H:%M")
    return (
        f"[Agent recently sent via scheduled task ({from_time}), user may be replying to this]\n"
        f"{sent_content}"
    )


def _build_system_prompt():
    now_str = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    parts = [
        f"You are the user's private AI agent and local system operator.\n"
        f"Current time: {now_str}\n"
        f"OPERATING SYSTEM: macOS (darwin)\n"
        f"Your role is to ACT and EXECUTE instructions on the host machine using your tools.\n"
        f"When a user asks for file operations, system tasks, or information retrieval, "
        f"ALWAYS prioritize calling tools to perform the action rather than just explaining how to do it.\n"
        f"You have direct access to the workspace and shell via tools.\n"
    ]
    for filename in ["SOUL.md", "AGENT.md", "USER.md"]:
        fpath = os.path.join(_workspace, filename)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    parts.append(f.read())
            except Exception:
                pass
    return "\n\n---\n\n".join(parts)


# ============================================================
#  Tool Use Loop (Core)
# ============================================================

_chat_locks = {}
_chat_locks_lock = threading.Lock()


def _get_chat_lock(session_key):
    with _chat_locks_lock:
        if session_key not in _chat_locks:
            _chat_locks[session_key] = threading.Lock()
        return _chat_locks[session_key]


def chat(user_msg, session_key, images=None, on_log=None):
    """Tool use loop entry point. Thread-safe.
    on_log: callback function(text) for real-time progress.
    """
    lock = _get_chat_lock(session_key)
    with lock:
        return _chat_inner(user_msg, session_key, images, on_log)


def _chat_inner(user_msg, session_key, images=None, on_log=None):
    import time as _time
    t0 = _time.monotonic()

    def log_step(txt):
        if on_log:
            on_log(txt)
        log.info(f"[{session_key}] {txt}")

    messages = _load_session(session_key)
    # ... (rest of the prep logic)
    user_message = _build_user_message(user_msg, images)
    messages.append(user_message)

    system_prompt = _build_system_prompt()

    # Hook 1: memory retrieval
    try:
        from brain import memory as mem_mod
        query_text = user_msg if isinstance(user_msg, str) else ""
        mem_context = mem_mod.retrieve(query_text, session_key)
        if mem_context:
            system_prompt += "\n\n---\n\n" + mem_context
            log_step("Retrieved relevant long-term memories.")
    except Exception as e:
        log.error("[chat] memory retrieve error: %s" % e)

    # ... (rest of the loop)
    tool_defs = limbs_hub.get_definitions()
    ctx = {"owner_id": _owner_id, "workspace": _workspace, "session_key": session_key}
    max_iterations = 20
    t_llm_total = 0
    tool_count = 0

    log_step("Thought: Starting analysis...")
    for i in range(max_iterations):
        api_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            t_llm_s = _time.monotonic()
            response = _call_llm(api_messages, tool_defs)
            t_llm_total += (_time.monotonic() - t_llm_s) * 1000
            
            if not response or "choices" not in response:
                error_msg = response.get("error", {}).get("message", "Unknown API error")
                return f"Sorry, AI service error: {error_msg}"
        except Exception as e:
            log.error(f"[chat] LLM error: {e}", exc_info=True)
            return f"Sorry, AI service unavailable: {e}"

        msg = response["choices"][0]["message"]
        messages.append(_serialize_assistant_msg(msg))

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            _save_session(session_key, messages)
            return msg.get("content", "")

        # Handle tools
        for tc in tool_calls:
            tool_count += 1
            t_name = tc["function"]["name"]
            try:
                args_str = tc["function"]["arguments"]
                func_args = json.loads(args_str)
                if not isinstance(func_args, dict):
                    func_args = {}
                log_step(f"Action: Calling tool '{t_name}' with args {args_str[:100]}...")
            except:
                func_args = {}
                log_step(f"Action: Calling tool '{t_name}'...")

            confirm_experimental = bool(func_args.pop("confirm_experimental", False))
            tool_status = tool_quality_mod.get_tool_status(t_name)

            if tool_status.get("experimental") and not confirm_experimental:
                result = (
                    f"[error] tool '{t_name}' is experimental "
                    f"(calls={tool_status.get('calls', 0)}, "
                    f"success_rate={tool_status.get('success_rate', 0.0):.2f}). "
                    "Set confirm_experimental=true to continue."
                )
                log_step(
                    f"[tool_quality] blocked experimental tool '{t_name}' "
                    f"(success_rate={tool_status.get('success_rate', 0.0):.2f})"
                )
                tool_quality_mod.record_call(
                    tool_name=t_name,
                    ok=False,
                    blocked=True,
                    error="experimental confirmation required",
                )
            else:
                if tool_status.get("experimental") and confirm_experimental:
                    log_step(
                        f"[tool_quality] confirmed experimental tool '{t_name}' "
                        "(confirm_experimental=true)"
                    )
                try:
                    result = limbs_hub.execute(t_name, func_args, ctx)
                    log_step(f"Result: {str(result)[:100]}...")
                    is_ok = not (isinstance(result, str) and result.lstrip().startswith("[error]"))
                    tool_quality_mod.record_call(
                        tool_name=t_name,
                        ok=is_ok,
                        blocked=False,
                        error=str(result)[:500] if not is_ok else "",
                    )
                except Exception as e:
                    result = f"[error] tool execution failed: {e}"
                    log_step(f"Error: {e}")
                    tool_quality_mod.record_call(
                        tool_name=t_name,
                        ok=False,
                        blocked=False,
                        error=str(e),
                    )
            
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})

    return "Processing timed out (max iterations reached)."
