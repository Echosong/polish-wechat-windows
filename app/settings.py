# -*- coding: utf-8 -*-
"""设置持久化。key 硬约束（docs/KICKOFF.md #6）：只进环境变量，绝不落文件；其余设置落 config.json。

key 的持久化走 Windows 用户环境变量（注册表 HKCU\\Environment，跟 setx 写的是同一个地方）。
全程只有一把：起草/润色用的 LLM_API_KEY，跟选哪家来源无关。
读的时候先看进程环境，没有就直接读注册表——IDE 启动时把环境快照拿走了，之后再 Run 继承的还是旧环境，
只靠 os.environ 会「保存了下次打开还是没有」。"""
from __future__ import annotations

import ctypes
import json
import os
import sys  # 只为下面这一处：打包后 __file__ 指向临时解包目录，config.json 得放在 exe 旁边才存得住

from core.providers import CUSTOM, DRAFT_PROVIDERS, LEGACY, LLM_ENV

_ROOT = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
         else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG = os.path.join(_ROOT, "config.json")
_DEFAULT_RELATIONSHIP = "romantic partners"
_DEFAULT_CONTEXT = 10
_DEFAULT_DRAFT = "deepseek"


def _read(name: str, default=None):
    """读 config.json 里的一个字段；每次都重新读文件，改设置不用重启进程。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            value = json.load(f).get(name)
    except (OSError, ValueError):
        return default
    return default if value is None else value

def _raw() -> dict:
    """config.json 的原始 dict（读不到或者坏掉一律当空）。改文件前先读它，别把不认识的字段抹掉。"""
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    with open(_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _friends_raw() -> dict:
    """friends 那一段的原始 dict（形状不对就当空）；改完原样写回去，顺手带上别的字段。"""
    book = _raw().get("friends")
    return dict(book) if isinstance(book, dict) else {}


def relationship() -> str:
    return str(_read("relationship") or _DEFAULT_RELATIONSHIP)

def context() -> int:
    """参考上下文条数：起草和判断各看最近多少条消息。3~30，缺失/脏数据一律退默认值。"""
    try:
        n = int(_read("context", _DEFAULT_CONTEXT))
    except (TypeError, ValueError):
        return _DEFAULT_CONTEXT
    return max(3, min(30, n))

def style() -> str:
    """用户自己描述的说话风格（可选，自由文本），只喂给起草模型。默认空 = 只照着最近的消息模仿。"""
    return str(_read("style") or "")

# ---------------------------------------------------------------- 按好友（按会话）单独一套偏好

def friends() -> dict:
    """按会话单独存的那几套偏好：{会话名: {relationship, style, context}}。

    键就是微信顶部的会话标题（好友名 / 群名），跟全局那几个字段互不影响；这里没有的会话走全局。"""
    return {str(k): v for k, v in _friends_raw().items() if str(k).strip() and isinstance(v, dict)}

def friend(title: str) -> dict:
    """这个会话单独设过的那几项；没设过就是空 dict（= 一切都跟全局一样）。"""
    return dict(friends().get(str(title or "").strip()) or {})

def relationship_for(title: str) -> str:
    """生成 / 润色用哪个关系背景：这个好友单独设过就用它的，没设过用全局。"""
    return str(friend(title).get("relationship") or "").strip() or relationship()

def style_for(title: str) -> str:
    """同上，说话风格。"""
    return str(friend(title).get("style") or "").strip() or style()

def context_for(title: str) -> int:
    """同上，参考上下文条数（3~30）。脏数据一律退全局。"""
    try:
        n = int(friend(title).get("context"))
    except (TypeError, ValueError):
        return context()
    return max(3, min(30, n))

def set_friend(title: str, *, relationship_text: str | None = None,
               style_text: str | None = None, context_n: int | None = None) -> None:
    """给一个会话存一套单独的偏好。跟 save() 一个口径：传 None = 这项不动，传空串 / 0 = 这项回全局。

    三项都空 = 整条删掉。只动 friends 这一段，别的字段原样保留——别为了一个好友把整份设置重写一遍。"""
    name = str(title or "").strip()
    if not name:
        return
    book = _friends_raw()
    stored = book.get(name)
    profile = dict(stored) if isinstance(stored, dict) else {}
    if relationship_text is not None:
        profile.pop("relationship", None)
        value = str(relationship_text).strip()
        if value:
            profile["relationship"] = value
    if style_text is not None:
        profile.pop("style", None)
        value = str(style_text).strip()
        if value:
            profile["style"] = value
    if context_n is not None:
        profile.pop("context", None)
        try:
            n = int(context_n)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            profile["context"] = max(3, min(30, n))
    if profile:
        book[name] = profile
    else:
        book.pop(name, None)
    data = _raw()
    data["friends"] = book
    _write(data)

def remove_friend(title: str) -> None:
    """删掉一个会话的单独偏好，回全局那套。"""
    name = str(title or "").strip()
    if not name:
        return
    book = _friends_raw()
    book.pop(name, None)
    data = _raw()
    data["friends"] = book
    _write(data)

def draft_provider() -> str:
    """起草走哪家（见 core/providers.DRAFT_PROVIDERS）。老配置里的 openrouter/deepseek 照样认。"""
    v = _read("draft_provider")
    return v if v in DRAFT_PROVIDERS else _DEFAULT_DRAFT

def draft_provider_name() -> str:
    return DRAFT_PROVIDERS[draft_provider()].name

def draft_model() -> str:
    """起草模型 id；空 = 用该来源的默认模型（有的来源没有默认，那就得自己选）。"""
    return str(_read("draft_model") or "") or DRAFT_PROVIDERS[draft_provider()].default

def draft_base_url() -> str:
    """自定义来源的 Base URL；其余来源用表里的，这里返回空。"""
    return str(_read("draft_base_url") or "") if draft_provider() in CUSTOM else ""

def reply_target() -> bool:
    """群聊指定回复对象：开了才在界面上选回复给谁、才把对象喂给模型。默认关。"""
    return bool(_read("reply_target", False))

def thinking() -> bool:
    """起草时是否开思考模式：慢且贵，默认关。只有 DeepSeek / OpenRouter / Anthropic / Gemini 吃它。"""
    return bool(_read("thinking", False))

def check_update() -> bool:
    """启动时要不要去 GitHub 查一次最新版本号：默认开，只出这一次网，设置里能关。"""
    return bool(_read("check_update", True))

def debug_view() -> bool:
    """调试视图：另开一个窗口实时画识别框。默认关，开了子进程才往队列里送帧。"""
    return bool(_read("debug_view", False))

def dock() -> bool:
    """悬浮框自动吸附在聊天窗口右侧：默认开。关了就固定在原地，只用拖动挪位置。"""
    return bool(_read("dock", True))

def _read_env(env_name: str) -> str:
    """进程环境优先；没有就读注册表并带进进程环境，之后 core/ 里按 os.environ 读就有了。"""
    v = os.environ.get(env_name, "").strip()
    if not v:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = str(winreg.QueryValueEx(k, env_name)[0]).strip()
        except Exception:  # 非 Windows / 没这个值
            v = ""
        if v:
            os.environ[env_name] = v
    return v

def _get_key(env_name: str) -> str:
    """语言模型那把 key。新名字空着就退回老版本按来源存的变量（下次保存会抄进新名字）。"""
    names = [env_name, LEGACY[env_name]]
    for name in names:
        if name and _read_env(name):
            return _read_env(name)
    return ""

def _set_key(env_name: str, value: str) -> None:
    """只写进程环境 + HKCU\\Environment，不写任何文件。"""
    os.environ[env_name] = value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, env_name, 0, winreg.REG_SZ, value)
    except Exception:
        pass  # 非 Windows（本机 Mac 开发）走不到，忽略


def _notify_env() -> None:
    """告诉别的进程环境变量变了。不能用 SendMessageTimeout 对 HWND_BROADCAST：
    它会逐个窗口等回复，超时 5 秒还按窗口数累加，保存按钮在界面线程上就卡死。
    SendNotifyMessage 把消息交出去就返回。"""
    try:
        fn = ctypes.windll.user32.SendNotifyMessageW
        fn.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p)
        fn.restype = ctypes.c_int
        fn(0xFFFF, 0x001A, 0, "Environment")  # HWND_BROADCAST, WM_SETTINGCHANGE
    except Exception:
        pass

def llm_key() -> str:
    """起草/润色那把 key，所有语言模型来源共用。"""
    return _get_key(LLM_ENV)

def has_llm_key() -> bool:
    return bool(llm_key())

def has_key() -> bool:
    """界面上「配没配好」问的就是这把 key。"""
    return has_llm_key()

def save(relationship_text: str | None = None, context_n: int | None = None, *,
         draft_provider_text: str | None = None,
         llm_key_text: str | None = None, draft_model_text: str | None = None,
         draft_base_url_text: str | None = None, reply_target_on: bool | None = None,
         style_text: str | None = None, thinking_on: bool | None = None,
         check_update_on: bool | None = None, debug_view_on: bool | None = None,
         dock_on: bool | None = None) -> None:
    """每个参数为空/None = 保留当前值。key 写进程环境 + HKCU\\Environment，不写任何文件。"""
    draft = draft_provider_text if draft_provider_text in DRAFT_PROVIDERS else draft_provider()
    # 这次填了就存这次填的；本来就配过就什么都不动；新名字还空着才从老名字迁一次
    if llm_key_text:
        value = llm_key_text
    elif _read_env(LLM_ENV):
        value = ""
    else:
        value = _read_env(LEGACY[LLM_ENV]) or ""
    if value:
        _set_key(LLM_ENV, value)
        _notify_env()
    n = context() if context_n is None else max(3, min(30, int(context_n)))
    # 空串 = 清掉，None = 原样留着（读原始字段，别读补过默认值的那个）
    keep = lambda new, name: str(_read(name) or "") if new is None else str(new).strip()
    flag = lambda new, now: now() if new is None else bool(new)
    # 整个 dict 必须在 open(..., "w") **之前**拼好：open 一上来就把文件截断，
    # 之后再 _read() 读到的是空文件，None 那几项就不是「保留」而是被清空了。
    data = {
        # 关系为空 = 只改别的开关（调试视图那种单项保存），别把它写没了
        "relationship": relationship_text or relationship(), "context": n,
        "style": keep(style_text, "style"),
        "draft_provider": draft, "draft_model": keep(draft_model_text, "draft_model"),
        "draft_base_url": keep(draft_base_url_text, "draft_base_url"),
        "reply_target": flag(reply_target_on, reply_target),
        "thinking": flag(thinking_on, thinking),
        "check_update": flag(check_update_on, check_update),
        "debug_view": flag(debug_view_on, debug_view),
        "dock": flag(dock_on, dock),
        # 按好友（按会话）的独立设置整段原样带过去，别在这一次全局保存里被抹掉
        "friends": _friends_raw(),
    }
    _write(data)
