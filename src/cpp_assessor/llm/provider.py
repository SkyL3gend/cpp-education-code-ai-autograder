"""OpenAI 兼容的对话补全客户端（只用于模式二的适配生成）。

设计约束（与架构方案 §3.6/§5.5 一致）：

- LLM 只产出"适配声明"（JSON），不产出评分结论；
- 调用日志只记录模型、耗时、token、提示词/响应的 sha256 与长度，
  **不落盘 API Key、不落盘完整提示词**；
- Key 优先来自显式参数，其次环境变量 ``ASSESSOR_LLM_API_KEY``，
  最后读 ``%USERPROFILE%\\.cpp-assessor\\llm.env``（仓库外，不进版本库）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.errors import AssessorError

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_S = 120
DEFAULT_MAX_TOKENS = 8192

ENV_FILE = Path.home() / ".cpp-assessor" / "llm.env"

_REDACT = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


class LLMError(AssessorError):
    """模型不可用、返回不是 JSON、或超出重试次数。"""
    def __init__(self, message: str, *, retryable: bool = True, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


def redact(text: str) -> str:
    """把可能出现的 Key 形状字符串替换掉，供日志/界面使用。"""
    return _REDACT.sub("sk-***", text or "")


def _key_from_env_file(path: Path | None = None) -> str:
    target = path or ENV_FILE
    try:
        if not target.is_file():
            return ""
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("ASSESSOR_LLM_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


@dataclass(frozen=True)
class LLMSettings:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = field(default="", repr=False)
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.0

    @classmethod
    def from_env(
        cls,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_s: int | None = None,
        max_tokens: int | None = None,
    ) -> "LLMSettings":
        key = (
            api_key
            or os.environ.get("ASSESSOR_LLM_API_KEY", "")
            or _key_from_env_file()
        ).strip()
        return cls(
            base_url=(base_url or os.environ.get("ASSESSOR_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            model=(model or os.environ.get("ASSESSOR_LLM_MODEL") or DEFAULT_MODEL).strip(),
            api_key=key,
            timeout_s=int(timeout_s or os.environ.get("ASSESSOR_LLM_TIMEOUT") or DEFAULT_TIMEOUT_S),
            max_tokens=int(max_tokens or os.environ.get("ASSESSOR_LLM_MAX_TOKENS") or DEFAULT_MAX_TOKENS),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> dict[str, Any]:
        """界面上展示用；绝不返回 Key 本身。"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "configured": self.configured,
            "key_hint": ("sk-***" + self.api_key[-4:]) if self.api_key else "",
        }


@dataclass
class LLMResponse:
    data: dict[str, Any]
    text: str
    duration_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    call_name: str = ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


class LLMClient:
    """极简客户端：一次请求一个 JSON 对象，失败有限重试。"""

    def __init__(self, settings: LLMSettings, *, log_dir: Path | None = None, max_attempts: int = 3):
        if not settings.configured:
            raise LLMError(
                "没有可用的模型 API Key：请在 LLM 模式页面填写，或设置环境变量 ASSESSOR_LLM_API_KEY"
            )
        self.settings = settings
        self.log_dir = Path(log_dir) if log_dir else None
        self.max_attempts = max(1, max_attempts)
        self.calls: list[dict[str, Any]] = []
        self.checkpoint = lambda: None
        self.deadline = float("inf")
        self.transport_attempts = 0

    # -- 对外接口 ---------------------------------------------------------

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        call_name: str = "generate_bundle",
        max_tokens: int | None = None,
    ) -> LLMResponse:
        last_error: str = ""
        last_exception: LLMError | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.checkpoint()
            if self.transport_attempts >= 18:
                raise LLMError("LLM 传输请求预算耗尽", retryable=False)
            self.transport_attempts += 1
            try:
                response = self._post(system, user, max_tokens or self.settings.max_tokens, call_name)
            except LLMError as exc:
                last_error = str(exc)
                last_exception = exc
                if not exc.retryable or attempt >= self.max_attempts:
                    break
                for _ in range(15 * attempt):
                    self.checkpoint()
                    time.sleep(0.1)
                continue
            text = _strip_fence(response.text)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                last_error = f"模型输出不是合法 JSON：{exc.msg}（片段：{redact(text[:160])!r}）"
                if attempt >= self.max_attempts:
                    break
                # 明确要求"只输出 JSON"再试一次。
                user = user + "\n\n注意：上一次输出不是合法 JSON。只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。"
                continue
            if not isinstance(data, dict):
                last_error = "模型输出的 JSON 不是对象"
                if attempt >= self.max_attempts:
                    break
                continue
            response.data = data
            return response
        if last_exception is not None and last_error == str(last_exception):
            raise last_exception
        raise LLMError(last_error or "模型调用失败")

    # -- 内部实现 ---------------------------------------------------------

    def _post(self, system: str, user: str, max_tokens: int, call_name: str) -> LLMResponse:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.settings.api_key}",
                "Accept": "application/json",
            },
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=max(0.1, min(self.settings.timeout_s, self.deadline - time.monotonic()))) as handle:
                raw = handle.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:  # pragma: no cover - 读取错误体失败时忽略
                detail = ""
            code = None
            if exc.code == 402:
                message = "模型账户余额或额度不足，请在模型服务商处检查账户，或改用可用的 API Key。程序尚未完成本次测评。"
                code = "MODEL_BALANCE_INSUFFICIENT"
            elif exc.code in {401, 403}:
                message = "模型服务拒绝访问，请检查 API Key 是否有效，以及该账户是否有模型访问权限。"
                code = "MODEL_ACCESS_DENIED"
            elif exc.code == 429:
                message = "模型服务请求过于频繁或已达调用限额，请稍后重试。"
                code = "MODEL_RATE_LIMITED"
            else:
                message = f"模型接口返回 HTTP {exc.code}：{redact(detail)}"
            self._log(call_name, system, user, "", started, ok=False, error=message)
            raise LLMError(message, retryable=exc.code == 429 or exc.code >= 500, code=code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            message = f"连不上模型接口：{redact(str(exc))}"
            self._log(call_name, system, user, "", started, ok=False, error=message)
            raise LLMError(message) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            document = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            message = f"模型接口返回的不是 JSON：{redact(str(exc))}"
            self._log(call_name, system, user, "", started, ok=False, error=message)
            raise LLMError(message) from exc

        try:
            text = document["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            message = f"模型接口返回结构异常：{redact(json.dumps(document, ensure_ascii=False)[:300])}"
            self._log(call_name, system, user, "", started, ok=False, error=message)
            raise LLMError(message) from exc

        usage = document.get("usage") or {}
        response = LLMResponse(
            data={},
            text=text if isinstance(text, str) else json.dumps(text, ensure_ascii=False),
            duration_ms=duration_ms,
            usage=usage if isinstance(usage, dict) else {},
            model=str(document.get("model") or self.settings.model),
            call_name=call_name,
        )
        self._log(call_name, system, user, response.text, started, ok=True, response=response)
        return response

    def _log(
        self,
        call_name: str,
        system: str,
        user: str,
        text: str,
        started: float,
        *,
        ok: bool,
        error: str = "",
        response: LLMResponse | None = None,
    ) -> None:
        record = {
            "call": call_name,
            "model": self.settings.model,
            "ok": ok,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "prompt_chars": len(system) + len(user),
            "prompt_sha256": _sha(system + "\n" + user),
            "response_chars": len(text),
            "response_sha256": _sha(text) if text else None,
            "usage": response.usage if response else {},
            "error": redact(error)[:500] if error else None,
        }
        self.calls.append(record)
        if self.log_dir is None:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with (self.log_dir / "llm_calls.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            return
