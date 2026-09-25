# -*- coding: utf-8 -*-
"""用合成数据预览 Qt 界面；不采集、不联网、不操作真实微信。

    python tools/preview_ui.py --state ready
    python tools/preview_ui.py --state ready --screenshot docs/ui_home.png

演示设置只保存在内存，不读取真实密钥，也不修改环境变量或 config.json。
「获取模型」按钮也走得通：列模型的接口被换成了本地假列表，全程不联网。
「生成回复」「润色」「发送」只往状态栏写一行演示提示，不碰微信。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import patch

# 直接 `python tools/preview_ui.py` 跑时，sys.path[0] 是 tools/，仓库根不在里面
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFontDatabase  # noqa: E402

from app import settings  # noqa: E402


_STATES = ("ready", "typed", "sending", "loading", "error", "setup", "settings", "paused", "debug", "details")

# 调试视图预览用的真微信截图（只读进内存，不改不存）；没有就退一张空画面
_FRAME = Path("/private/tmp/claude-501/-Users-lpitiless-Documents-project-wechatjev"
              "/26954c2b-b4b9-432e-bad7-0d0b803e4309/images/9.png")
_AREA = (433, 152, 1298, 767)  # 那张图里的消息区，头部从 y=40 起
# 消息区裁剪坐标的框：前面这些是真跑一遍 OCR 得到的，灰字/小字那两个是手摆的，凑齐六种颜色
_BOXES = [
    (83, 99, 156, 122, "name", "Asterlion"),
    (26, 131, 70, 146, "image", "借仲夏夜之梦"),
    (99, 136, 143, 162, "her", "难绷"),
    (83, 194, 156, 218, "name", "Asterlion"),
    (101, 233, 315, 256, "her", "怎么识别到仲夏夜之梦的（"),
    (612, 30, 700, 44, "gray", "链接卡片的灰字"),
    (612, 50, 690, 62, "tiny", "表情包里的小字"),
    (633, 298, 760, 328, "me", "好像识别头像了"),
    (685, 368, 762, 399, "me", "笑死我了"),
    (85, 432, 157, 454, "name", "Asterlion"),
    (27, 464, 70, 478, "image", "借仲夏夜之梦"),
    (99, 467, 159, 493, "her", "还真是"),
    (98, 563, 160, 592, "her", "哈哈哈"),
]
_LINES = [("her", "Asterlion", "难绷"), ("her", "Asterlion", "怎么识别到仲夏夜之梦的（"),
          ("me", None, "好像识别头像了"), ("me", None, "笑死我了"),
          ("her", "Asterlion", "还真是"), ("her", "Asterlion", "哈哈哈")]


def _debug_packet():
    """合成一份子进程会发的调试包。QImage 读 PNG 进内存取 RGB 裸字节（行有 4 字节对齐，按行裁）。"""
    import time

    from PySide6.QtGui import QImage

    img = QImage(str(_FRAME)) if _FRAME.exists() else QImage()
    if img.isNull():
        img = QImage(1303, 979, QImage.Format_RGB888)
        img.fill(0x202524)
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    rgb = b"".join(bytes(img.constScanLine(y))[:w * 3] for y in range(h))
    return {"w": w, "h": h, "rgb": rgb, "scale": 1, "area": _AREA, "pane_top": 40,
            "title": "白金搬砖小分队", "boxes": _BOXES, "lines": _LINES,
            "ocr_ms": 261, "ts": time.time()}

_CHAT = "白金搬砖小分队"  # 演示里「微信当前开着的」会话：用群聊，回复对象那一行才看得见
# (会话, 谁, 内容, 群里的发言人, 时间)：两个会话，下拉框里都能看到
_MESSAGES = (
    ("白金搬砖小分队", "her", "周末有人去爬山吗", "阿杰", "09:12"),
    ("白金搬砖小分队", "me", "我有空，几点集合？", "", "09:15"),
    ("白金搬砖小分队", "her", "我也去，带上我一个", "陈与小金", "09:15"),
    ("白金搬砖小分队", "her", "八点地铁口见，记得带水", "阿杰", "09:16"),
    (_CHAT, "me", "有空呀，还是上次那家？", "", "18:43"),
    (_CHAT, "her", "好呀！六点见怎么样？我好久没吃了 😋", "", "18:43"),
)
_GROUP = "白金搬砖小分队"
_SENDERS = ("阿杰", "陈与小金")  # 最近说话的排最前，跟 main.py 那边一个口径

# 「生成回复」拿回来的 3 条：第 1 条是它自己最推荐的，另两条挂成备选
_REPLIES = [
    "好呀，六点见！我这就出门",
    "可以，六点在上次那家？我先去占个位",
    "行，六点见 😋",
]
# 用户自己手打的那版，「润色」前长这样
_TYPED = "那个 六点行 我先过去看看有没有位置 你慢慢来 不用急"


def register_fallback_fonts(app) -> int:
    """没装系统字体时补一遍（离屏/无头环境、CI）：不注册的话中文全是方块。返回加进去的字体数。

    正常桌面跑不需要——Qt 自己就能看见系统字体；这里只是兜底，加了也不影响已有字体。
    """
    root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
    added = 0
    for folder in (root, local):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() in (".ttf", ".ttc", ".otf"):
                if QFontDatabase.addApplicationFont(str(path)) != -1:
                    added += 1
    return added


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用合成聊天预览 Qt UI；绝不采集、联网或操作真实微信。"
    )
    parser.add_argument("--state", choices=_STATES, default="ready", help="预览界面状态")
    parser.add_argument("--width", type=int, help="窗口宽度（试紧凑布局用，比如 320）")
    parser.add_argument("--height", type=int, help="窗口高度（试矮窗口用，比如 380）")
    parser.add_argument("--screenshot", metavar="PATH", help="将演示界面保存为 PNG 后退出（合成数据，不含微信内容）")
    args = parser.parse_args()
    target = Path(args.screenshot).expanduser() if args.screenshot else None

    # 演示里：生成/润色走 DeepSeek 官网，key 当「已配置」
    configured = "" if args.state == "setup" else "demo-key"
    demo_settings = {"relationship": "friends", "context": 10, "llm_key": configured,
                     "draft_provider": "deepseek", "draft_model": "deepseek-flash",
                     "draft_base_url": "", "reply_target": True, "dock": True,
                     "style": "话少，基本不用标点，急了才发感叹号", "thinking": False,
                     "check_update": True, "debug_view": args.state == "debug"}

    def save_demo_settings(relationship_text=None, context_n=None, *, draft_provider_text=None,
                           llm_key_text=None, draft_model_text=None, draft_base_url_text=None,
                           reply_target_on=None, style_text=None, thinking_on=None,
                           check_update_on=None, debug_view_on=None, dock_on=None):
        if relationship_text:
            demo_settings["relationship"] = relationship_text
        if context_n is not None:
            demo_settings["context"] = context_n
        for name, value in (("draft_provider", draft_provider_text), ("draft_model", draft_model_text),
                            ("draft_base_url", draft_base_url_text), ("style", style_text)):
            if value is not None:
                demo_settings[name] = value
        if llm_key_text:
            demo_settings["llm_key"] = llm_key_text
        for name, value in (("reply_target", reply_target_on), ("thinking", thinking_on),
                            ("check_update", check_update_on), ("debug_view", debug_view_on),
                            ("dock", dock_on)):
            if value is not None:
                demo_settings[name] = bool(value)

    def fake_llm_models(protocol, base_url, api_key, timeout=10, headers=None):
        """演示不联网：给一小撮假模型，让「获取模型」按钮在本地也走得通。"""
        return {"anthropic": ["claude-demo-4", "claude-demo-4-mini"],
                "gemini": ["gemini-demo-pro", "gemini-demo-flash"]}.get(
            protocol, ["deepseek-flash", "deepseek-reasoner", "demo-model-a", "demo-model-b"])

    # 在创建 Overlay 前替换设置接口，整个事件循环期间都保持隔离。
    with patch("core.llm.list_models", fake_llm_models), patch.multiple(
        settings,
        has_key=lambda: bool(demo_settings["llm_key"]),
        has_llm_key=lambda: bool(demo_settings["llm_key"]),
        llm_key=lambda: demo_settings["llm_key"],
        relationship=lambda: demo_settings["relationship"],
        context=lambda: demo_settings["context"],
        draft_provider=lambda: demo_settings["draft_provider"],
        draft_model=lambda: demo_settings["draft_model"],
        draft_base_url=lambda: demo_settings["draft_base_url"],
        reply_target=lambda: demo_settings["reply_target"],
        style=lambda: demo_settings["style"],
        thinking=lambda: demo_settings["thinking"],
        check_update=lambda: demo_settings["check_update"],
        debug_view=lambda: demo_settings["debug_view"],
        dock=lambda: demo_settings["dock"],
        save=save_demo_settings,
    ):
        from PySide6.QtCore import QTimer
        from app.overlay import Overlay

        def demo_generate(chat):
            # 真跑的时候这里是两次网络调用；演示里等半秒直接把合成结果放上去。
            QTimer.singleShot(600, lambda: (
                ov.set_replies(_REPLIES),
                ov.set_status("演示模式：生成好了 3 条，下面还有 2 条备选；发送只做演示提示。", kind="success")))

        def demo_polish(text, chat):
            QTimer.singleShot(600, lambda: (
                ov.set_polished("六点我可以，先过去看看有没有位置，你慢慢来就行"),
                ov.set_status("演示模式：润色好了；点「还原」能换回你自己写的那版。", kind="success")))

        def demo_send(text, chat):
            QTimer.singleShot(300, lambda: ov.sent(
                True, f"演示模式：已模拟发送「{text}」；未操作微信。"))

        def demo_polish_send(text, chat):
            def polished():
                ov.send_polished("六点我可以，先过去看看有没有位置，你慢慢来就行")
                QTimer.singleShot(300, lambda: ov.sent(
                    True, "演示模式：已模拟发送润色后的那版；未操作微信。"))
            QTimer.singleShot(600, polished)

        ov = Overlay(on_generate=demo_generate, on_polish=demo_polish, on_send=demo_send,
                     on_polish_and_send=demo_polish_send)
        register_fallback_fonts(ov.app)
        if args.width or args.height:  # 试紧凑/矮窗口，看按钮会不会被挤掉
            ov.win.resize(args.width or ov.win.width(), args.height or ov.win.height())
        ov.win.setWindowTitle("润色 · 界面演示（合成数据）")
        shot = ov.win  # 截图截哪个窗口；调试预览截调试窗

        if args.state == "debug":
            from app.debugwin import DebugWindow

            dbg = DebugWindow(on_close=lambda: ov.set_debug_switch(False))
            dbg.setWindowTitle("识别调试 · 界面演示（合成数据）")
            dbg.show_packet(_debug_packet())
            dbg.show()
            shot = dbg
        else:
            for chat, who, text, name, timestamp in _MESSAGES:
                ov.log_message(who, text, name, timestamp=timestamp, chat=chat)
            ov.set_targets(_GROUP, _SENDERS, _SENDERS[0])  # 群聊才有回复对象这一行
            ov.set_chat(_CHAT)
            if args.state == "setup":
                ov.set_status("演示模式：还没配模型，先设置再生成。", kind="warning")
                ov.open_settings()
            elif args.state == "paused":
                ov.set_capture(False)
            elif args.state == "typed":
                ov.input.setPlainText(_TYPED)
                ov.set_status("演示模式：自己写完点「润色」，只改怎么说、不改说什么。")
            elif args.state == "sending":
                ov.input.setPlainText(_TYPED)
                ov._send_with_polish()  # 「润色并发送」：润好了直接发，不再等用户点第二次
            elif args.state == "settings":
                ov.open_settings()
            elif args.state == "details":
                ov._toggle_details()  # 会话详情默认收着，这一档是「展开以后」的样子
                ov.set_status("演示模式：会话详情展开后才有「对方最近说」和聊天记录。")
            else:
                ov.set_replies(_REPLIES)
                ov.set_update("9.9.9", "https://github.com/Echosong/polish-wchat-windows/releases/latest")
                if args.state == "loading":
                    ov.set_busy(True, "正在结合上下文写回复…")
                elif args.state == "error":
                    ov.set_status("生成失败：调用模型 HTTP 401: 密钥被拒", kind="error")

        exit_code = 0
        if target is not None:
            def save_screenshot():
                nonlocal exit_code
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not shot.grab().save(str(target), "PNG"):
                        raise OSError(f"无法保存截图：{target}")
                    print(f"已保存合成界面截图：{target}")
                except OSError as exc:
                    print(str(exc))
                    exit_code = 1
                finally:
                    ov.app.quit()

            QTimer.singleShot(500, save_screenshot)
        ov.run()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
