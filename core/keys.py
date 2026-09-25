# -*- coding: utf-8 -*-
"""调模型这条链路的公共零件：key 读取、报错脱敏、异常类型。

key 只从环境变量（或 Windows 用户注册表 HKCU\\Environment）读，绝不落文件、绝不进日志
（docs/KICKOFF.md 硬约束 #6）。这里不认识任何「来源」——来源表在 core/providers.py，
协议怎么调在 core/llm.py。
"""
from __future__ import annotations

import os
from typing import NoReturn

try:  # 当模块导入 / 当脚本直接跑 都能用
    from .providers import ENV_VARS, LEGACY, LLM_ENV
except ImportError:
    from providers import ENV_VARS, LEGACY, LLM_ENV


class ChatError(Exception):
    """调模型失败。status = HTTP 状态码，拿不到就是 None。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def redact_secrets(text: str) -> str:
    """把任何字符串里出现的 key 换成 [REDACTED]，再打印或落盘。"""
    if not isinstance(text, str):
        text = str(text)
    for env in ENV_VARS:
        key = os.environ.get(env) or ""
        if key:
            text = text.replace(key, "[REDACTED]")
    return text


def status_of(exc: Exception) -> int | None:
    """各家 SDK 放 HTTP 状态码的属性名不一样：openai/anthropic 是 status_code，
    google-genai 是 code（它的 status 是 'NOT_FOUND' 这种字符串）。"""
    for name in ("status_code", "code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


def fail(exc: Exception, what: str) -> NoReturn:
    """SDK 抛的异常 → 一句人话的 ChatError。消息过脱敏，绝不把 key 带出来。"""
    if isinstance(exc, ChatError):
        raise exc
    status = status_of(exc)
    hint = {401: "密钥被拒", 403: "没有权限", 404: "模型或地址不对", 422: "请求被拒",
            429: "被限流", 529: "服务过载"}.get(status, "")
    detail = redact_secrets(str(exc)).strip()[:300]
    head = f"{what} HTTP {status}" if status else f"{what}失败"
    raise ChatError(f"{head}: {hint or detail or type(exc).__name__}", status) from None


def api_key(env: str = LLM_ENV, fallback: str | None = None) -> str:
    """语言模型那把 key（LLM_API_KEY）。新名字空着就退回老名字，老用户不用重填。

    fallback = 某个来源自带的变量名，在那里配过的用户不用再填一遍。
    """
    names = [env, LEGACY.get(env, ""), fallback or ""]
    for name in names:
        key = (os.environ.get(name) or "").strip() if name else ""
        if key:
            return key
    raise ChatError(
        f"{' / '.join(n for n in names if n)} is not set. Export it in the environment; "
        "do not put the key in a file."
    )


if __name__ == "__main__":
    # ponytail: 不联网，只验「key 取哪一把」和「报错里不许出现 key」这两条硬约束。
    os.environ["LLM_API_KEY"] = "sk-new"
    assert api_key() == "sk-new"
    os.environ.pop("LLM_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = "sk-old"
    assert api_key() == "sk-old"  # 老变量兜底
    assert api_key(fallback="DASHSCOPE_API_KEY") == "sk-old"  # 新名空 → 老名 → 来源自带
    assert "sk-old" not in redact_secrets("boom sk-old boom")
    assert "[REDACTED]" in redact_secrets("boom sk-old boom")

    class _Boom(Exception):
        status_code = 401

    try:
        fail(_Boom("bad key sk-old"), "润色")
        raise SystemExit("应当抛错")
    except ChatError as e:
        assert e.status == 401 and "密钥被拒" in str(e) and "sk-old" not in str(e)

    os.environ.pop("DEEPSEEK_API_KEY")
    try:
        api_key()
        raise SystemExit("应当抛错")
    except ChatError as e:
        assert "LLM_API_KEY" in str(e)
    print("keys ok")
