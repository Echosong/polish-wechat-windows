# -*- coding: utf-8 -*-
"""父进程：只管界面和网络调用。截图 + OCR 在 app/worker.py 的子进程里跑，队列里收聊天记录。

**不自动发送**：点「发送」才粘进微信并按一次回车，整个程序只有这一处会真的发出去，没有开关。
草稿默认也不自动写（对方来新消息只记账，不出网、不调模型，静默期零调用）；输入区那个
「自动生成」开关打开后，对方一发新消息就自动起草一版填进输入框——发送仍然要用户自己按。
上下文和聊天记录都按会话名（子进程 OCR 头部标题得来）分开存，切会话不串味；
关系背景 / 说话风格 / 参考上下文也按会话取（设置页「按好友设置」里单独设过就用它的，没设过用全局）。

    pip install rapidocr-onnxruntime numpy windows-capture PySide6-Fluent-Widgets
模型的来源和 key 在独立设置页填写，不用改代码。IDE 里直接 Run。
"""
import ctypes
import multiprocessing
import queue
import threading
import traceback
from collections import deque

from app import settings, update, worker
from app.capture import find_wechat_hwnd, window_rect
from app.fill import send
from app.overlay import Overlay
from app.version import VERSION
from core.engine import generate, polish

# {会话名: {history, target, senders}}：每个会话各自的上下文，互不串味
# history 里是 [(who, text, name)]，模型只认 her/me，name 是群里的发言人（单聊/自己说的是 None）；
# 只是缓冲区，实际喂模型几条由设置里的「参考上下文」决定
# senders：这个群里发过言的人，去重、最近的排最前；target：用户挑的回复对象（None = 跟着最近那个走）
chats = {}
state = {"area": None, "busy": False, "hwnd": None, "chat": "",
         "send_after_polish": False}  # 最后那个：这次润色跑完要不要接着发出去（「润色并发送」）
jobs = queue.Queue()  # 后台线程 → 主线程：生成/润色/发送的结果
update_result = queue.Queue()  # 独立小队列，别跟 jobs 的三元组形状搅在一起


def chat_of(title):
    return chats.setdefault(title, {"history": deque(maxlen=60), "target": None, "senders": []})


def target_of(title):
    """这个会话现在的回复对象：用户挑过且人还在就用它，否则用最近说话的那个；单聊没有发言人 → None。"""
    chat = chat_of(title)
    if chat["target"] in chat["senders"]:
        return chat["target"]
    return chat["senders"][0] if chat["senders"] else None


def reply_to_of(title):
    """群聊指定了回复对象才传，其余情况 None（= 正常回复）。"""
    return target_of(title) if settings.reply_target() else None


def out_text(title, text):
    """真要发出去的文字：群聊里勾了带 @ 就在前面加「@名字 」（纯文本，微信不认成真正的 @）。"""
    if not (settings.reply_target() and ov.at_prefix_enabled()):
        return text
    target = target_of(title)
    return f"@{target} " + text if target else text


def spawn_worker():
    """开一个采集子进程，它跟着 capture_on 走：置位=采集，清掉=暂停。"""
    p = multiprocessing.Process(target=worker.run,
                                args=(q, state["hwnd"], capture_on, debug_on), daemon=True)
    p.start()
    return p


def set_debug(on):
    """调试视图开关：开 → 开窗 + 置位（子进程这才开始送帧，一帧 2~3MB）；关 → 清掉 + 收窗。"""
    global dbg
    if not on:
        debug_on.clear()
        if dbg is not None:
            dbg.hide()
        return
    if dbg is None:
        from app.debugwin import DebugWindow

        dbg = DebugWindow(on_close=on_debug_closed)
    dbg.show()
    debug_on.set()


def on_debug_closed():
    """用户直接关了调试窗 = 把开关也关了，否则设置页显示开着但没窗。"""
    debug_on.clear()
    ov.set_debug_switch(False)
    settings.save(debug_view_on=False)


def on_toggle_capture(on):
    """标题栏开关。启动时没找到微信就没有子进程，这会儿再找一次，找到了才真开得起来。"""
    global child
    if not on:
        capture_on.clear()
        return
    if child is None:
        try:
            state["hwnd"] = find_wechat_hwnd()
        except RuntimeError:
            ov.set_capture(False, "未找到聊天窗口，打开后再开启采集")
            return
        child = spawn_worker()
    capture_on.set()


# ------------------------------------------------------------------ 用户点按钮才做的事

def on_generate(title):
    """「生成回复」：带着这个会话的上下文，后台跑一次模型。"""
    if state["busy"]:
        return
    if not settings.has_key():
        ov.set_busy(False)
        ov.set_status("请先在设置里配置模型", "warning")
        return
    state["busy"] = True
    threading.Thread(target=generate_bg, args=(title, list(chat_of(title)["history"])),
                     daemon=True).start()


def on_polish(text, title):
    """「润色」：把用户自己写的那段改顺，也要看同一个会话的上下文。"""
    if state["busy"]:
        return
    if not settings.has_key():
        ov.set_busy(False)
        ov.set_status("请先在设置里配置模型", "warning")
        return
    state["busy"] = True
    threading.Thread(target=polish_bg, args=(text, title, list(chat_of(title)["history"])),
                     daemon=True).start()


def on_polish_and_send(text, title):
    """「润色并发送」/ Ctrl+Shift+回车：润一遍，回来直接发。
    只多一次润色调用，别的跟「自己点润色、再点发送」完全一样。"""
    if state["busy"]:
        return
    if not settings.has_key():
        ov.set_busy(False)
        ov.set_status("请先在设置里配置模型", "warning")
        return
    state["busy"] = True
    state["send_after_polish"] = True
    threading.Thread(target=polish_bg, args=(text, title, list(chat_of(title)["history"])),
                     daemon=True).start()


def on_target_change(title, name):
    """用户在群里挑了回复对象：记下来，下次生成/润色/发送都按 TA 走（不自动重跑）。"""
    chat_of(title)["target"] = name


def on_send(text, title):
    """「发送」：粘进微信输入框再按一次回车。整个流程只有这一处会真的发出去。"""
    threading.Thread(target=send_bg, args=(text, title), daemon=True).start()


def generate_bg(title, msgs):
    """后台线程只跑网络调用，结果丢队列；UI 只在主线程的 tick 里动（Qt 不能跨线程碰）。"""
    try:
        # 关系背景 / 说话风格 / 参考上下文都按会话取：这个好友单独设过就用它的，没设过用全局
        cands = generate(msgs, settings.relationship_for(title), context=settings.context_for(title),
                         model=settings.draft_model() or None,
                         provider=settings.draft_provider(),
                         base_url=settings.draft_base_url() or None,
                         reply_to=reply_to_of(title), style=settings.style_for(title),
                         thinking=settings.thinking())
        jobs.put(("replies", title, cands))
    except Exception as e:
        jobs.put(("failed", title, f"生成失败：{e}"))


def polish_bg(text, title, msgs):
    try:
        polished = polish(text, msgs, settings.relationship_for(title), context=settings.context_for(title),
                          model=settings.draft_model() or None,
                          provider=settings.draft_provider(),
                          base_url=settings.draft_base_url() or None,
                          reply_to=reply_to_of(title), style=settings.style_for(title),
                          thinking=settings.thinking())
        jobs.put(("polished", title, polished))
    except Exception as e:
        jobs.put(("failed", title, f"润色失败：{e}"))


def send_bg(text, title):
    try:
        if state["hwnd"] is None:  # 子进程重开过，hwnd 可能换了，用最新的
            raise RuntimeError("未找到聊天窗口，请确认已经打开")
        if state["area"] is None:
            raise RuntimeError("输入区域尚不可用，请确认聊天窗口可见（不要最小化）")
        send(state["hwnd"], state["area"], out_text(title, text))
    except Exception as e:
        jobs.put(("sent", False, f"发送失败：{e}"))
    else:
        jobs.put(("sent", True, "已发送"))


def check_update_bg():
    """启动时后台查一次新版本，跟生成一个套路：网络调用在线程里，UI 只在 tick() 里动。"""
    r = update.check_latest(VERSION)
    if r:
        update_result.put(r)


# ------------------------------------------------------------------ 主循环

def drain():
    """把子进程队列里攒的东西全收掉。新消息只记账；只有在「自动生成」开着、输入框还空着时才顺手起草一版。"""
    global child
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            return
        kind = msg[0]
        if kind == "area":  # 只是窗口挪了位置，坐标跟着更新，别的什么都不用动
            state["area"] = msg[1]
            continue
        if kind == "chat":  # 微信切了会话，界面跟过去（用户正浏览别的会话时也跟，微信是准的）
            state["chat"] = msg[1]
            ov.set_chat(msg[1])
            continue
        if kind == "debug":  # 调试视图的一帧；窗口不在就直接丢掉
            if dbg is not None:
                dbg.show_packet(msg[1])
            continue
        if kind == "status":  # 单帧识别失败/报错，提示一下就好，别把已知坐标清掉
            ov.set_status(msg[1], "warning")
            ov.log(msg[1])
            continue
        if kind == "paused":  # 子进程确认已暂停
            ov.set_capture(False)
            continue
        if kind == "resumed":  # 子进程重新开始采集
            ov.set_capture(True)
            continue
        if kind == "dead":  # 采集彻底停了（微信关了之类），这才是真的要清状态
            state["area"] = None
            ov.set_capture(False, msg[1])
            ov.log(msg[1])
            if child is not None:  # 子进程已经不干活了，收掉引用，下次打开开关重开一个
                child.terminate()
                child.join()
                child = None
            continue
        _, title, new, area = msg
        state["area"] = area
        chat = chat_of(title)
        for who, name, text in new:
            chat["history"].append((who, text, name))
            ov.log_message(who, text, name, chat=title)
            if who == "her" and name:  # 群里发过言的人，去重后最近的排最前
                if name in chat["senders"]:
                    chat["senders"].remove(name)
                chat["senders"].insert(0, name)
        ov.set_targets(title, chat["senders"], target_of(title))  # 显不显示这一行由悬浮窗按开关决定
        if title == ov.current_chat() and not state["busy"] and new[-1][0] == "her":
            if ov.auto_generate() and not ov.has_draft():  # 开着自动生成、输入框还空着，就起草一版
                ov.set_busy(True, "对方刚发来消息，自动起草中…")
                on_generate(title)
            else:
                ov.set_status("对方刚发来消息，写点什么或者点「生成回复」", "idle")


def tick():
    try:
        drain()
        while not update_result.empty():
            latest, url = update_result.get()
            ov.set_update(latest, url)
        while not jobs.empty():
            job = jobs.get()
            if job[0] == "sent":  # (kind, ok, message) 形状跟下面两个不一样，先分掉
                _, ok, message = job
                ov.sent(ok, message)
                continue
            _, title, payload = job
            state["busy"] = False
            ov.set_busy(False)
            send_now = state["send_after_polish"]  # 这一份结果是不是「润色并发送」要的
            state["send_after_polish"] = False
            if title and title != ov.current_chat():  # 生成期间切走了：这份结果对不上现在要发的人，丢掉
                if job[0] == "failed":
                    ov.polish_failed(payload, send_now)  # 别把「失败原因」吞掉，用户得知道为什么
                elif send_now:
                    ov.cancel_pending_send("已经切到别的会话，这次没发出去（润色结果也作废了）；切回去再点一次。")
                continue
            if job[0] == "replies":
                ov.set_replies(payload)
            elif job[0] == "polished":
                if send_now:
                    ov.send_polished(payload)  # 润好了直接往外发，不再等用户点一次
                else:
                    ov.set_polished(payload)
            else:
                ov.polish_failed(payload, send_now)
        if ov.docked():  # 吸附：聊天窗口挪了/缩了，悬浮框跟着贴过去
            ov.dock_to(window_rect(state["hwnd"]))
    except Exception:
        traceback.print_exc()  # 一帧出错不退出
    ov.after(50, tick)


if __name__ == "__main__":  # Windows 的 spawn 会让子进程重新执行本文件，没这行就无限套娃开进程
    multiprocessing.freeze_support()  # 打包成 exe 后 spawn 出来的子进程会重跑一遍 exe，没这行就无限弹界面
    ctypes.windll.user32.SetProcessDPIAware()
    q = multiprocessing.Queue()
    capture_on = multiprocessing.Event()  # 父子进程共用的开关，置位=采集
    debug_on = multiprocessing.Event()  # 同上，置位=子进程往队列里送整帧给调试窗
    ov = Overlay(on_generate=on_generate, on_polish=on_polish, on_send=on_send,
                 on_toggle_capture=on_toggle_capture, on_target_change=on_target_change,
                 on_toggle_debug=set_debug, on_polish_and_send=on_polish_and_send)
    child = dbg = None
    try:
        state["hwnd"] = find_wechat_hwnd()
    except RuntimeError:
        ov.set_capture(False, "未找到聊天窗口，打开后再开启采集")
    else:
        capture_on.set()
        child = spawn_worker()
    if settings.debug_view():  # 上次开着就直接开回来
        set_debug(True)
    if not settings.has_key():
        ov.after(0, ov.open_settings)
    if settings.check_update() and update.parse_version(VERSION):  # 开发版没有版本号，不查也不烦源码用户
        threading.Thread(target=check_update_bg, daemon=True).start()
    ov.after(50, tick)
    try:
        ov.run()
    finally:
        if child is not None:
            child.terminate()
