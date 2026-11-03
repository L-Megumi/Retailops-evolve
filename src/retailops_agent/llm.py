from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request


def _parse_json_object(content: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("模型返回的JSON不是对象")
    return parsed


class OpenAICompatibleModel:
    """Small dependency-free client for OpenAI-compatible chat endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30,
        max_tokens: int = 4096,
        thinking: bool | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.request_count = 0
        self.response_history: list[dict[str, object]] = []
        self._state_lock = threading.Lock()
        self._thread_state = threading.local()

    @property
    def last_usage(self) -> dict[str, object]:
        return getattr(self._thread_state, "last_usage", {})

    @classmethod
    def from_env(cls) -> "OpenAICompatibleModel":
        api_key = os.environ.get("RETAILOPS_API_KEY", "")
        if not api_key:
            raise ValueError("使用模型规划器时必须设置 RETAILOPS_API_KEY")
        return cls(
            base_url=os.environ.get("RETAILOPS_BASE_URL", "https://api.openai.com/v1"),
            api_key=api_key,
            model=os.environ.get("RETAILOPS_MODEL", "gpt-4.1-mini"),
            max_tokens=int(os.environ.get("RETAILOPS_MAX_TOKENS", "4096")),
        )

    @classmethod
    def deepseek_from_env(cls) -> "OpenAICompatibleModel":
        api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("RETAILOPS_API_KEY", "")
        if not api_key:
            raise ValueError("必须设置 DEEPSEEK_API_KEY 或 RETAILOPS_API_KEY")
        return cls(
            base_url=os.environ.get("RETAILOPS_BASE_URL", "https://api.deepseek.com"),
            api_key=api_key,
            model=os.environ.get("RETAILOPS_MODEL", "deepseek-flash"),
            timeout=float(os.environ.get("RETAILOPS_TIMEOUT", "90")),
            max_tokens=int(os.environ.get("RETAILOPS_MAX_TOKENS", "4096")),
            thinking=os.environ.get("RETAILOPS_THINKING", "disabled").lower()
            in {"1", "true", "enabled", "yes"},
        )

    def total_usage(self) -> dict[str, int]:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for response in self.response_history:
            usage = response.get("usage")
            if not isinstance(usage, dict):
                continue
            for key in totals:
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] += value
        return totals

    def complete_json(self, *, system: str, user: str) -> dict[str, object]:
        attempt_history: list[dict[str, object]] = []
        for attempt in range(2):
            reminder = (
                "\nJSON强制模式刚才返回了空内容。请直接输出一个非空JSON对象，"
                "不要使用Markdown代码块或添加解释。"
                if attempt
                else ""
            )
            body_payload: dict[str, object] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system + reminder},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "max_tokens": self.max_tokens,
            }
            if self.thinking is not None:
                body_payload["thinking"] = {
                    "type": "enabled" if self.thinking else "disabled"
                }
                body_payload["reasoning_effort"] = "high" if self.thinking else "none"
            if attempt == 0:
                body_payload["response_format"] = {"type": "json_object"}
            body = json.dumps(body_payload).encode("utf-8")
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            payload = None
            for network_attempt in range(3):
                with self._state_lock:
                    self.request_count += 1
                    request_id = self.request_count
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    break
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")
                    retryable = exc.code in {429, 500, 502, 503, 504}
                    with self._state_lock:
                        self.response_history.append(
                            {
                                "request": request_id,
                                "network_attempt": network_attempt + 1,
                                "http_error": exc.code,
                                "retryable": retryable,
                                "content_preview": detail[:300],
                                "usage": {},
                            }
                        )
                    if retryable and network_attempt < 2:
                        time.sleep(2**network_attempt)
                        continue
                    raise RuntimeError(
                        f"模型接口返回 HTTP {exc.code}: {detail[:300]}"
                    ) from exc
                except urllib.error.URLError as exc:
                    with self._state_lock:
                        self.response_history.append(
                            {
                                "request": request_id,
                                "network_attempt": network_attempt + 1,
                                "network_error": str(exc.reason)[:300],
                                "retryable": True,
                                "usage": {},
                            }
                        )
                    if network_attempt < 2:
                        time.sleep(2**network_attempt)
                        continue
                    raise RuntimeError(f"模型网络请求失败: {exc.reason}") from exc
                except TimeoutError as exc:
                    with self._state_lock:
                        self.response_history.append(
                            {
                                "request": request_id,
                                "network_attempt": network_attempt + 1,
                                "network_error": "response read timed out",
                                "retryable": True,
                                "usage": {},
                            }
                        )
                    if network_attempt < 2:
                        time.sleep(2**network_attempt)
                        continue
                    raise RuntimeError("模型响应读取超时") from exc
            if payload is None:  # pragma: no cover - every terminal path raises
                raise RuntimeError("模型请求未返回响应")
            usage = payload.get("usage") or {}
            self._thread_state.last_usage = usage
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            response_meta = {
                "request": request_id,
                "json_mode": attempt == 0,
                "finish_reason": choice.get("finish_reason"),
                "content_empty": not bool(content and content.strip()),
                "content_preview": content[:500] if content else "",
                "usage": usage,
            }
            attempt_history.append(response_meta)
            with self._state_lock:
                self.response_history.append(response_meta)
            if content and content.strip():
                try:
                    return _parse_json_object(content)
                except (json.JSONDecodeError, ValueError) as exc:
                    if attempt == 0:
                        continue
                    raise ValueError(
                        f"模型返回非JSON内容: {content[:200]!r}"
                    ) from exc
        last = attempt_history[-1] if attempt_history else {}
        raise ValueError(
            "模型连续两次返回空 JSON 内容；"
            f"finish_reason={last.get('finish_reason')!r}, usage={last.get('usage')!r}"
        )
