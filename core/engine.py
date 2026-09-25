# -*- coding: utf-8 -*-
"""整条链的唯一入口，两件事：

    generate(messages, relationship, ...) → 按对话写 3 条候选（第 1 条最推荐）
    polish(text, messages, relationship, ...) → 把用户自己写的草稿润色成接得上对话的那句话

两次调用都是一次模型请求。要不要调、什么时候调，全由用户点按钮决定——这里不含任何自动触发。
平台无关。悬浮窗和命令行 demo 都只调这两个函数。
"""
from __future__ import annotations

try:
    from .draft import draft_candidates, polish_text
except ImportError:
    from draft import draft_candidates, polish_text


def generate(messages: list, relationship: str, model: str | None = None,
             timeout: float = 30, context: int = 10, provider: str = "deepseek",
             base_url: str | None = None, reply_to: str | None = None, style: str = "",
             thinking: bool = False) -> list[str]:
    """按对话写候选回复。messages: [(from, text)] 或 [(from, text, name)]，最新一条在最后。

    返回 1~3 条中文候选（第 1 条是模型最推荐的），一条都没有会抛 ChatError。
    context: 看最近多少条消息（设置里的「参考上下文」）。
    provider: 走哪家（core.providers.DRAFT_PROVIDERS）；base_url 只有自定义来源要传。
    reply_to: 群聊里指定回复给谁；None = 正常回复。
    style: 用户自己描述的说话风格。thinking: 思考模式，默认关。model=None 用该来源的默认模型。
    """
    return draft_candidates(messages, relationship, provider=provider, model=model,
                            base_url=base_url, timeout=timeout, keep=context,
                            reply_to=reply_to, style=style, thinking=thinking)


def polish(text: str, messages: list, relationship: str, model: str | None = None,
           timeout: float = 30, context: int = 10, provider: str = "deepseek",
           base_url: str | None = None, reply_to: str | None = None, style: str = "",
           thinking: bool = False) -> str:
    """把 text（用户自己写的草稿）润色成接得上对话、像他本人说的话。参数含义同 generate()。

    只改「怎么说」，不改「说什么」——内容以 text 为准，这里不替用户加信息。
    """
    return polish_text(text, messages, relationship, provider=provider, model=model,
                       base_url=base_url, timeout=timeout, keep=context,
                       reply_to=reply_to, style=style, thinking=thinking)


if __name__ == "__main__":
    # ponytail: 不联网。两条链路都只是转发，验「参数有没有原样传下去」就够了。
    from unittest.mock import patch

    seen: dict = {}

    def fake_draft(messages, relationship, **kw):
        seen["draft"] = (messages, relationship, kw)
        return ["甲", "乙", "丙"]

    def fake_polish(text, messages, relationship, **kw):
        seen["polish"] = (text, messages, relationship, kw)
        return "改好的"

    with patch("__main__.draft_candidates", fake_draft), patch("__main__.polish_text", fake_polish):
        msgs = [("her", "在吗")]
        assert generate(msgs, "friends", provider="openai", model="m", context=6) == ["甲", "乙", "丙"]
        assert seen["draft"][2]["provider"] == "openai" and seen["draft"][2]["keep"] == 6
        assert seen["draft"][2]["model"] == "m" and seen["draft"][2]["reply_to"] is None
        assert polish("我写的", msgs, "friends", style="话少", thinking=True) == "改好的"
        assert seen["polish"][0] == "我写的" and seen["polish"][3]["style"] == "话少"
        assert seen["polish"][3]["thinking"] is True
    print("engine ok")
