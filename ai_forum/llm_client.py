"""Independent LLM client for AI forum posting/replying."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger("ai_forum")


class ForumLLMClient:
    def __init__(
        self,
        models_config: Dict[str, Any],
        poster_model: Optional[str] = None,
        reviewer_model: Optional[str] = None,
    ):
        self._models = models_config or {}
        self._poster_model = poster_model
        self._reviewer_model = reviewer_model

    def generate_post(self, open_thread_count: int) -> Dict[str, str]:
        provider_name = self._resolve_provider_name(self._poster_model)
        prompt = (
            "你是 developer_ai，正在一个 AI 协作论坛同步开发进展。"
            "请生成一条新的中文帖子，面向 reviewer_ai。"
            "\n要求："
            "\n1) 标题 12-28 字；"
            "\n2) 正文 80-220 字；"
            "\n3) 包含当前工作项、风险、下一步；"
            "\n4) 语气务实，不要寒暄。"
            f"\n当前未回复帖子数: {open_thread_count}"
            "\n仅返回 JSON 对象："
            '{"title":"...","body":"..."}'
        )

        content = self._chat(
            [
                {"role": "system", "content": "你是严谨的软件开发记录助手。"},
                {"role": "user", "content": prompt},
            ],
            provider_name,
        )

        data = _parse_json_object(content)
        title = str(data.get("title", "")).strip()
        body = str(data.get("body", "")).strip()
        if not title or not body:
            raise ValueError("LLM post result missing title/body")
        return {"title": title[:120], "body": body[:4000]}

    def generate_reply(self, thread: Dict[str, Any]) -> str:
        provider_name = self._resolve_provider_name(self._reviewer_model)
        prompt = (
            "你是 reviewer_ai，职责是检查未回复开发帖并给出测试/产品视角反馈。"
            "\n请基于以下帖子进行回帖："
            f"\n标题：{thread.get('title', '')}"
            f"\n正文：{thread.get('body', '')}"
            "\n要求："
            "\n1) 输出中文；"
            "\n2) 60-220 字；"
            "\n3) 包含：你理解的目标、发现的风险、一个可执行的下一步验证建议；"
            "\n4) 不要输出 Markdown 标题。"
            "\n仅返回 JSON 对象："
            '{"reply":"..."}'
        )

        content = self._chat(
            [
                {"role": "system", "content": "你是严谨的产品与测试协作助手。"},
                {"role": "user", "content": prompt},
            ],
            provider_name,
        )

        data = _parse_json_object(content)
        reply = str(data.get("reply", "")).strip()
        if not reply:
            raise ValueError("LLM reply result missing body")
        return reply[:4000]

    def _resolve_provider_name(self, preferred: Optional[str]) -> str:
        providers = self._models.get("providers", {})
        if not providers:
            raise ValueError("models.providers is empty")

        if preferred:
            if preferred not in providers:
                raise ValueError(f"configured model '{preferred}' not found in providers")
            return preferred

        default_name = self._models.get("default")
        if default_name not in providers:
            raise ValueError(f"models.default '{default_name}' not found in providers")
        return default_name

    def _chat(self, messages: List[Dict[str, str]], provider_name: str) -> str:
        providers = self._models.get("providers", {})
        provider = providers[provider_name]

        if provider.get("type") == "azure":
            endpoint = provider["api_base"].rstrip("/")
            deployment = provider["deployment_name"]
            api_version = provider.get("api_version", "2024-05-01-preview")
            url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
            headers = {
                "Content-Type": "application/json",
                "api-key": provider["api_key"],
            }
        else:
            url = provider["api_base"].rstrip("/") + "/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {provider['api_key']}",
            }

        body: Dict[str, Any] = {
            "messages": messages,
            "max_tokens": provider.get("max_tokens", 1024),
            "temperature": provider.get("temperature", 0.7),
        }

        if provider.get("type") != "azure":
            body["model"] = provider["model"]

        extra = provider.get("extra_body") or {}
        body.update(extra)

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        timeout = provider.get("timeout", 90)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            error_body = ""
            try:
                error_body = err.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {err.code}: {error_body}") from err

        try:
            return payload["choices"][0]["message"].get("content", "")
        except Exception as exc:
            raise RuntimeError("invalid LLM response format") from exc


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidate = cleaned[start : end + 1]
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data

    log.error("Failed to parse JSON object from LLM response: %s", cleaned[:300])
    raise ValueError("LLM response is not a JSON object")
