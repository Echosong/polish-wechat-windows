# -*- coding: utf-8 -*-
"""浅色置顶悬浮框：挂在聊天窗口旁边，手动生成 / 手动润色 / 手动发送。

不自动分析：新消息只记进上下文和聊天记录，调不调模型、发不发，全看用户点没点按钮。
「润色并发送」和它的开关也只改变「按发送键之后先润色再发」，一样要用户自己按。
窗口默认吸附在聊天窗口右侧（屏幕右边放不下就翻到左边），拖一下就脱开，点图钉再吸回来。
"""
import threading
from datetime import datetime
from types import SimpleNamespace

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QPushButton, QSizeGrip, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CardWidget, CheckBox, ComboBox, EditableComboBox, FluentIcon as FIF,
    HyperlinkButton, IndeterminateProgressBar, LineEdit, PasswordLineEdit, PlainTextEdit,
    PrimaryPushButton, PushButton, ScrollArea, SpinBox, SwitchButton, Theme, TransparentToolButton,
    setCustomStyleSheet, setFont, setTheme, setThemeColor,
)

from app import settings
from app.version import VERSION
from core import llm, providers

_LOG_LINES = 300
_MUTED = "#68776f"
_GREEN = "#18794e"
_CHAT_GAP = 8      # 吸附时跟聊天窗口之间留的空隙
_INPUT_H = 96      # 输入框高度（默认那种一条的写长句子太挤）
_BUTTON_H = 38     # 生成回复 / 润色 / 润色并发送 / 发送 四个按钮的高度（比控件默认高一档）
_FEED_H = 180      # 记录区高度：窗口高度跟着内容走，所以这里给个定值，别让它俩互相追着变
_SETTINGS_H = 640  # 设置页给这么高的窗口（表单长，再高屏幕也放不下，里面本来就有滚动条）
# 界面上的名字就用这两个汉字；polish-chat 那个英文名只出现在文件名、exe、发布包和仓库名上
_APP_NAME = "润色"
_RELATIONSHIPS = [
    ("恋人", "romantic partners"), ("朋友", "friends"), ("同事", "colleagues"),
    ("家人", "family"), ("自定义", None),
]


class _FitCombo(ComboBox):
    """长名字不撑开窄布局。按钮上按当前宽度省略；条目仍是全文，findText 靠它。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setText(self, text):
        self._full = text or ""
        QPushButton.setText(self, self._elide(self._full))
        if self._full and self.text() != self._full:
            self.setToolTip(self._full)

    def minimumSizeHint(self):
        hint = QPushButton.minimumSizeHint(self)
        return QSize(48, hint.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        shown = self._elide(self._full)
        if shown != self.text():
            QPushButton.setText(self, shown)
        if self._full and shown != self._full:
            self.setToolTip(self._full)

    def _elide(self, text):
        # 右侧箭头大约 28px。还没排上版时先按一个窄宽度省略，避免最小宽度被整句名字撑开。
        avail = self.width() - 36 if self.width() > 64 else 120
        return self.fontMetrics().elidedText(text, Qt.ElideRight, max(24, avail))


def _label(text="", size=14, color=None, bold=False, parent=None):
    label = BodyLabel(text, parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    setFont(label, size, QFont.DemiBold if bold else QFont.Normal)
    if color:
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(label, qss, qss)
    return label


def _tool(icon, title, callback, parent=None):
    button = TransparentToolButton(icon, parent)
    button.setFixedSize(32, 32)
    button.setToolTip(title)
    button.setAccessibleName(title)
    button.clicked.connect(callback)
    return button


def _short(text, limit=12):
    """按钮上的短标题：太长就截断，全文挂 tooltip。"""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


class _Surface(CardWidget):
    def __init__(self, parent=None, accent=False):
        self.accent = accent
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def _normalBackgroundColor(self):
        return QColor("#edf7f0" if self.accent else "#ffffff")

    def _hoverBackgroundColor(self):
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self):
        return self._normalBackgroundColor()


class _Fetched(QObject):
    """取模型列表的后台线程 → 主线程：哪一组（SimpleNamespace）、取回来的模型 id、失败原因（成功是空串）。
    Qt 不让跨线程碰控件，信号是跨线程唯一干净的路。"""
    done = Signal(object, list, str)


class _TitleBar(QWidget):
    """只有标题栏可拖动，选择正文或按按钮不会意外移动窗口。

    按下后要先真的挪出 _DRAG_SLOP 像素才算拖动：点一下、手抖两像素不该把窗口挪走，
    更不该顺手把吸附关掉（标题栏在最上面，很容易被误点）。"""
    _DRAG_SLOP = 6

    def __init__(self, parent, on_drag=None):
        super().__init__(parent)
        self._drag = None
        self._moving = False
        self._on_drag = on_drag

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window().pos()
            self._moving = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            target = event.globalPosition().toPoint() - self._drag
            if not self._moving:
                if (target - self.window().pos()).manhattanLength() < self._DRAG_SLOP:
                    return  # 还在手的抖动范围内，先不动
                self._moving = True
                if self._on_drag:
                    self._on_drag()  # 真拖了：脱开吸附（只影响这次运行，不动存的设置）
            self.window().move(target)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        self._moving = False
        super().mouseReleaseEvent(event)


class _MainWindow(QWidget):
    """窗口大小变了就叫 Overlay 重新排布；断点没跨过时 _relayout 自己不做事，这里不用防抖。"""
    def __init__(self, relayout):
        super().__init__()
        self._relayout = relayout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width(), event.size().height())


class Overlay:
    def __init__(self, on_generate, on_polish, on_send, on_toggle_capture=None,
                 on_target_change=None, on_toggle_debug=None, on_polish_and_send=None):
        """on_generate(会话名) → 用户点了「生成回复」。
        on_polish(草稿, 会话名) → 用户点了「润色」。
        on_send(文字, 会话名) → 用户点了「发送」（父进程负责粘贴 + 回车）。
        on_polish_and_send(草稿, 会话名) → 用户点了「润色并发送」/ 按了 Ctrl+Shift+回车，
        或者开了「发送前润色」后点了「发送」；父进程润完直接发，发失败会把结果留在输入框里。
        on_target_change(会话名, 人名) → 用户在群里挑了回复对象。
        on_toggle_debug(开不开) → 开关调试视图那个独立窗口。"""
        self.app = QApplication.instance() or QApplication([])
        setTheme(Theme.LIGHT)
        setThemeColor(_GREEN, save=False)
        self.on_generate = on_generate
        self.on_polish = on_polish
        self.on_send = on_send
        # 没传就退回「润了就填进框里、发送还是自己按」：老调用方（预览脚本等）不用跟着改
        self.on_polish_and_send = on_polish_and_send or (lambda text, title: self.on_polish(text, title))
        self.on_toggle_capture = on_toggle_capture
        self.on_target_change = on_target_change
        self.on_toggle_debug = on_toggle_debug
        self.alts = []          # 备选回复（不含已经放进输入框的那条）
        self._busy = False
        self._undo = None       # 上一次被覆盖掉的内容，「还原」用
        self._compact = None    # 断点模式：None 保证 _relayout 第一次调用必定生效
        self._pageLayouts = []
        self._hintLabels = []
        self._altButtons = []
        self.feeds = {}         # {会话名: [排好版的记录]}
        self.counts = {}        # {会话名: 消息条数}
        self.hers = {}          # {会话名: 对方最近一句}
        self.targets = {}       # {会话名: ([发言人], 当前回复对象)}
        self._chat = ""         # 微信当前开着的会话
        self._shown = ""        # 界面上正在看的会话（浏览时和上面不一样）
        self._details_open = False  # 会话详情（对方最近说 + 聊天记录）默认收着，首页只留标题那一行
        self._feed_open = False     # 聊天记录自己还有一层展开，收起详情时它不单独露头
        self._expanded_h = None     # 铺开时那个高：收起再展开要回到原来那个高度
        self._docked = settings.dock()
        self._pending_send = None  # 正在等润色结果的那段原文：润色回来就直接发，不用再点一次
        self._rect = None       # 上次用过的聊天窗口位置，没变就不折腾
        self.win = _MainWindow(self._relayout)
        self.win.setObjectName("assistantWindow")
        self.win.setWindowTitle(_APP_NAME)
        self.win.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.win.setStyleSheet(
            "QWidget#assistantWindow { background: #f5f7f6; border: 1px solid #dce3de; border-radius: 14px; }"
        )
        self.win.setMinimumWidth(320)
        self.win.setMaximumWidth(640)
        outer = QVBoxLayout(self.win)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        header = _TitleBar(self.win, on_drag=self._undock)
        self.header = header  # 算窗口高度时要问它有多高
        title = QHBoxLayout(header)
        title.setContentsMargins(18, 12, 10, 10)
        title.setSpacing(8)
        name = _label(_APP_NAME, 20, "#233c2f", True)
        name.setFixedWidth(52)
        name.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.addWidget(name)
        self.subtitle = _label(f"v{VERSION}", 12, _MUTED)
        self.subtitle.setWordWrap(False)  # 窄窗口里别让版本号把标题撑开
        self.subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.addWidget(self.subtitle, 1)
        self.dockButton = TransparentToolButton(FIF.PIN, header)
        self.dockButton.setFixedSize(32, 32)
        self.dockButton.setCheckable(True)
        self.dockButton.setChecked(self._docked)
        self.dockButton.setToolTip("吸附在聊天窗口旁边（拖动窗口会自动脱开）")
        self.dockButton.setAccessibleName("吸附在聊天窗口旁边")
        self.dockButton.clicked.connect(lambda *_: self._dock_toggled(self.dockButton.isChecked()))
        title.addWidget(self.dockButton)
        self.settingsButton = _tool(FIF.SETTING, "设置", self.open_settings, header)
        title.addWidget(self.settingsButton)
        title.addWidget(_tool(FIF.REMOVE, "最小化", self.win.showMinimized, header))
        title.addWidget(_tool(FIF.CLOSE, "关闭助手", self.win.close, header))
        outer.addWidget(header)
        self.updateBar = QWidget(self.win)
        update_row = QHBoxLayout(self.updateBar)
        update_row.setContentsMargins(18, 4, 8, 4)
        update_row.setSpacing(8)
        self.updateLabel = _label("", 12, _GREEN, True)
        update_row.addWidget(self.updateLabel, 1)
        self.updateLink = HyperlinkButton("", "去下载", self.updateBar)
        self.updateLink.setFixedHeight(24)
        update_row.addWidget(self.updateLink)
        closeUpdate = TransparentToolButton(FIF.CLOSE, self.updateBar)
        closeUpdate.setFixedSize(20, 20)
        closeUpdate.setToolTip("关闭更新提示")
        closeUpdate.setAccessibleName("关闭更新提示")
        closeUpdate.clicked.connect(self._close_update)
        update_row.addWidget(closeUpdate)
        self.updateBar.setFixedHeight(32)
        self.updateBar.hide()
        outer.addWidget(self.updateBar)
        self.pages = QStackedWidget(self.win)
        outer.addWidget(self.pages, 1)
        self._build_home()
        self._build_settings()
        self._build_composer()  # 输入框和三个按钮钉在窗口底部，不跟首页一起滚走
        outer.addWidget(self.composer)
        self.footer = QWidget(self.win)
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(20, 9, 8, 8)
        self.footerLabel = _label("", 11, _MUTED)
        footer.addWidget(self.footerLabel, 1)
        grip = QSizeGrip(self.footer)
        grip.setFixedSize(16, 16)
        footer.addWidget(grip, 0, Qt.AlignBottom)
        outer.addWidget(self.footer)
        screen = self.app.primaryScreen().availableGeometry()
        width = min(420, screen.width() - 32)
        height = min(self._fit_height(), screen.height() - 48)  # 默认收着，窗口就开成内容高
        self.win.setMinimumHeight(height)  # 再矮就把输入框和三个按钮挤没了
        self.win.resize(width, height)
        # 默认是吸附的，所以开机先落在屏幕右下角（跟吸附后的观感一致）：等聊天窗口坐标一到就贴过去，
        # 中间不出现「先冒在顶上再跳下来」那一下。QRect.right()/bottom() 是闭区间，末尾那个 +1 别省。
        self.win.move(screen.right() - width - 20 + 1, screen.bottom() - height - 20 + 1)
        self._relayout(width, height)  # resizeEvent 补不到构造时这一次
        self._sendShortcuts()
        self._render_footer()
        self._render_buttons()
        self._apply_capture_text(self.captureSwitch.isChecked())
        self.win.show()

    def _sendShortcuts(self):
        """Ctrl+回车 = 发送；Ctrl+Shift+回车 = 润色并发送（只这一次，不动那个开关）。
        Enter 仍然是换行，写长句子不会手一抖发出去。"""
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), self.win)
            shortcut.activated.connect(self._send)
        for keys in ("Ctrl+Shift+Return", "Ctrl+Shift+Enter"):
            shortcut = QShortcut(QKeySequence(keys), self.win)
            shortcut.activated.connect(self._send_with_polish)

    def _scroll_page(self):
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("pageContent")
        content.setStyleSheet("QWidget#pageContent { background: transparent; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 8, 20, 12)
        layout.setSpacing(12)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        self._pageLayouts.append(layout)
        return scroll, layout

    def _relayout(self, w, h):
        """宽度跨过断点才重新摆布局（省事）；高度只在用户自己拉过时记一笔。"""
        compact = w < 400
        if compact != self._compact:
            self._compact = compact
            self._apply_compact(compact)
            if not self._details_open:
                self._apply_height()  # 紧凑模式的页边距小一点，收着的时候顺手把窗口也收回去
        # 只有首页那个高才算「铺开时的高度」；设置页、窄成一个条的那种都不算
        if (self.pages.currentWidget() is self.home and self._details_open
                and h > self._fit_height()):
            self._expanded_h = h  # 用户自己拉过的高：收起来再展开还用这个，不被挤回去

    def _apply_compact(self, compact):
        """紧凑/常规两套间距和可见性；断点没变时不会被调用。"""
        self.subtitle.setVisible(not compact)
        self.autoSwitch.setOnText("" if compact else "自动生成")
        self.autoSwitch.setOffText("" if compact else "手动生成")
        self.captureSwitch.setOnText("" if compact else "采集中")
        self.captureSwitch.setOffText("" if compact else "已暂停")
        for label in self._hintLabels:
            label.setVisible(not compact)
        margins = (12, 8, 12, 12) if compact else (20, 8, 20, 12)
        for layout in self._pageLayouts:
            layout.setContentsMargins(*margins)
        self._render_alts()  # 窄了按钮上的字要更短，重排一次
        self._render_buttons()

    # ---------------------------------------------------------------- 窗口高度

    def _work_area(self):
        """窗口现在在哪块屏上，就按哪块屏的可用区域算。"""
        screen = QGuiApplication.screenAt(self.win.frameGeometry().center()) or self.app.primaryScreen()
        return screen.availableGeometry()

    def _fit_height(self):
        """把看得见的那几块高度加起来：窗口高度就照这个来，不留一大片空白。

        页那一段要拿 ScrollArea 的最小高（68）兜底，不能只看页里内容有多高：页先按自己的最小高拿走
        一份，剩下的才轮到输入区，算少了输入区就被挤到最小高以下——Qt 那时会把里面的控件**摞在一起**
        （实测就是三个按钮盖到输入框上、进度条横穿按钮，用户截图那种「错乱」）。
        """
        height = self.header.sizeHint().height() + self.footer.sizeHint().height()
        if not self.updateBar.isHidden():
            height += self.updateBar.height()
        page = max(self.pages.minimumSizeHint().height(), self.home.widget().sizeHint().height())
        composer = max(self.composer.sizeHint().height(), self.composer.minimumSizeHint().height())
        margins = self.win.layout().contentsMargins()
        return height + page + composer + margins.top() + margins.bottom()

    def _desired_height(self):
        """收着就是内容高；铺开着用你自己拉过的高（比内容还高才用），没拉过也是内容高。"""
        height = self._fit_height()
        return max(self._expanded_h or height, height) if self._details_open else height

    def _apply_height(self):
        """按当前状态把窗口高度收放一次：收着矮、铺开高，全都是内容说了算。
        设置页是长表单、有自己的滚动条，别拿它的高度去撑窗口。

        内容一变（出备选、进度条出来、状态文字换行、群聊多一行回复对象、展开详情、更新提示）就得再调一次，
        不然窗口停在旧高度、里面被挤成一团。
        """
        if self.pages.currentWidget() is not self.home:
            return
        height = self._desired_height()
        self.win.setMinimumHeight(height)  # 下限一起顶到内容高：拿尺寸手柄也拉不出「控件摞一起」那种状态
        self.win.resize(self.win.width(), min(height, self._work_area().height()))
        self._realign()  # 高度变了下沿就不在聊天窗口那条线上了，重贴一次

    def _realign(self):
        """吸附着的时候按记下来的聊天窗口矩形重贴一次（「没动就不折腾」那条会挡住，先清掉）。"""
        if not self._docked or not self._rect:
            return
        rect, self._rect = self._rect, None
        self.dock_to(rect)

    # ---------------------------------------------------------------- 首页

    def _build_home(self):
        self.home, body = self._scroll_page()
        chat_row = QHBoxLayout()
        chat_row.setSpacing(8)
        prefix = _label("当前会话", 12, _MUTED)
        prefix.setFixedWidth(56)
        chat_row.addWidget(prefix)
        self.chatBox = _FitCombo()
        self.chatBox.setPlaceholderText("尚未识别到会话")
        self.chatBox.setAccessibleName("当前会话")
        self.chatBox.setToolTip("聊天窗口切到哪个会话这里就跟到哪个；也可以自己选一个，只读它的上下文")
        self.chatBox.currentIndexChanged.connect(self._on_chat_selected)
        chat_row.addWidget(self.chatBox, 1)
        self.countLabel = _label("", 11, _MUTED)  # 条数跟在会话标题后面：收着时也知道记了多少条
        self.countLabel.setWordWrap(False)
        chat_row.addWidget(self.countLabel)
        self.chatFollow = _label("", 11, _MUTED)
        self.chatFollow.setFixedWidth(52)
        self.chatFollow.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        chat_row.addWidget(self.chatFollow)
        self.detailsButton = _tool(FIF.CHEVRON_RIGHT_MED, "展开会话详情", self._toggle_details)
        chat_row.addWidget(self.detailsButton)
        body.addLayout(chat_row)
        self.targetRow = QWidget()  # 只有开了「群聊指定回复对象」且这个会话是群聊才露出来
        target_row = QHBoxLayout(self.targetRow)
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(8)
        target_prefix = _label("回复对象", 12, _MUTED)
        target_prefix.setFixedWidth(56)
        target_row.addWidget(target_prefix)
        self.targetBox = _FitCombo()
        self.targetBox.setAccessibleName("回复对象")
        self.targetBox.setToolTip("生成和润色都按这个人来写；不选就跟着最近说话的那位")
        self.targetBox.currentIndexChanged.connect(self._on_target_selected)
        target_row.addWidget(self.targetBox, 1)
        self.atCheck = CheckBox("带 @")
        self.atCheck.setChecked(True)
        self.atCheck.setToolTip("发送时在开头加「@名字 」。只是普通文字，不会变成真正的 @")
        target_row.addWidget(self.atCheck)
        self.targetRow.hide()
        body.addWidget(self.targetRow)

        self.context = QWidget()
        context_box = QVBoxLayout(self.context)
        context_box.setContentsMargins(0, 0, 0, 0)
        context_box.setSpacing(3)
        context_box.addWidget(_label("对方最近说", 11, _MUTED))
        self.latest = _label("", 14, "#42574a")
        self.latest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        context_box.addWidget(self.latest)
        self.context.hide()
        body.addWidget(self.context)

        self.historyButton = PushButton(FIF.HISTORY, "聊天记录")
        self.historyButton.clicked.connect(self._toggle_history)
        self.historyButton.setAccessibleName("展开或收起聊天记录")
        body.addWidget(self.historyButton)
        self.feed = PlainTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setPlaceholderText("识别到的聊天内容会显示在这里")
        self.feed.setMaximumBlockCount(_LOG_LINES)
        self.feed.setFixedHeight(_FEED_H)
        self.feed.hide()
        body.addWidget(self.feed)
        self._render_details()  # 默认收起：首页只剩「当前会话 + 条数」这一行
        self._render_count()
        body.addStretch(1)

    def _build_composer(self):
        """底部常驻的输入区：输入框 + 生成回复 / 润色 / 发送 / 润色并发送 + 备选 + 状态行。
        放在滚动页外面，窗口再矮也够得着这几个按钮。

        按钮排成两行：第一行「生成回复 + 润色」= 让 AI 写点什么；第二行「发送 + 润色并发送」
        = 把它发出去，区别只在发之前改不改。四个按钮挤一行在 420 宽的默认窗口里就只剩不到 80px 一个，
        「润色并发送」会在按钮上被截掉。"""
        self.composer = _Surface()
        box = QVBoxLayout(self.composer)
        box.setContentsMargins(14, 12, 14, 10)
        box.setSpacing(8)
        self.input = PlainTextEdit()
        self.input.setPlaceholderText("在这里写你要发的话；或者点「生成回复」让它先写一版。")
        self.input.setAccessibleName("要发送的内容")
        self.input.setFixedHeight(_INPUT_H)
        self.input.textChanged.connect(self._on_text_changed)
        box.addWidget(self.input)
        first = QHBoxLayout()
        first.setSpacing(8)
        self.generateButton = PrimaryPushButton("生成回复")
        self.generateButton.setAccessibleName("按当前对话生成回复")
        self.generateButton.clicked.connect(self._generate)
        first.addWidget(self.generateButton, 1)
        self.polishButton = PushButton("润色")
        self.polishButton.setAccessibleName("把输入框里的话润色得接得上对话")
        self.polishButton.setToolTip("把你写好的这段话改顺，让它接得上对话、像你本人说的；不改你的意思")
        self.polishButton.clicked.connect(self._polish)
        first.addWidget(self.polishButton, 1)
        box.addLayout(first)
        second = QHBoxLayout()
        second.setSpacing(8)
        self.sendButton = PushButton("发送")
        self.sendButton.setAccessibleName("发送到聊天窗口")
        self.sendButton.clicked.connect(self._send)
        second.addWidget(self.sendButton, 1)
        self.sendPolishButton = PushButton("润色并发送")
        self.sendPolishButton.setAccessibleName("润色后直接发送到聊天窗口")
        self.sendPolishButton.clicked.connect(self._send_with_polish)
        second.addWidget(self.sendPolishButton, 1)
        box.addLayout(second)
        for button in (self.generateButton, self.polishButton, self.sendButton, self.sendPolishButton):
            button.setFixedHeight(_BUTTON_H)  # 四个按钮比默认高一点：输入框上去了，按钮别还是细细一条
        self.altRow = QWidget()
        alt_row = QHBoxLayout(self.altRow)
        alt_row.setContentsMargins(0, 0, 0, 0)
        alt_row.setSpacing(6)
        self.altLabel = _label("换一条", 11, _MUTED)
        self.altLabel.setWordWrap(False)  # 挤窄了就换行会顶成两行，宁可让它截断
        self.altLabel.setFixedWidth(40)
        alt_row.addWidget(self.altLabel)
        self.altLayout = alt_row
        self.altRow.hide()
        box.addWidget(self.altRow)
        status_row = QHBoxLayout()  # 状态行：左边一句状态，右边两个开关（标题栏只放标题，别挤它）
        status_row.setSpacing(8)
        self.status = _label("", 12, _MUTED)
        status_row.addWidget(self.status, 1)
        self.autoSwitch = SwitchButton(self.composer)
        self.autoSwitch.setOnText("自动生成")
        self.autoSwitch.setOffText("手动生成")
        self.autoSwitch.setChecked(False)  # 默认关：不点按钮就一次模型都不调，跟以前的承诺一样
        self.autoSwitch.setToolTip(
            "开：对方一发新消息就自动起草一版，只填进输入框、不会自动发送（发送永远要你自己按）")
        self.autoSwitch.setAccessibleName("自动生成回复")
        self.autoSwitch.checkedChanged.connect(self._auto_toggled)
        status_row.addWidget(self.autoSwitch)
        self.captureSwitch = SwitchButton(self.composer)
        self.captureSwitch.setOnText("采集中")
        self.captureSwitch.setOffText("已暂停")
        self.captureSwitch.setToolTip("开启或暂停采集：暂停后不再读聊天内容")
        self.captureSwitch.setAccessibleName("开启或暂停采集")
        self.captureSwitch.setChecked(True)
        self.captureSwitch.checkedChanged.connect(self._capture_toggled)
        status_row.addWidget(self.captureSwitch)
        box.addLayout(status_row)
        self.progress = IndeterminateProgressBar()
        self.progress.setFixedHeight(3)
        self.progress.hide()
        box.addWidget(self.progress)

    # ---------------------------------------------------------------- 设置页

    def _build_settings(self):
        self.settingsPage, body = self._scroll_page()
        heading = QHBoxLayout()
        heading.addWidget(_tool(FIF.RETURN, "返回", self._back_home))
        heading.addWidget(_label("设置", 23, "#24382d", True), 1)
        body.addLayout(heading)
        body.addWidget(_label("调整关系背景和口吻，配置生成/润色用的那个模型。", 13, _MUTED))
        preference = _Surface()
        box = QVBoxLayout(preference)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_label("回复偏好", 16, "#304c3c", True))
        relation_label = _label("你们的关系", 13)
        box.addWidget(relation_label)
        self.relationshipBox = ComboBox()
        self.relationshipBox.setMinimumWidth(0)
        self.relationshipBox.addItems([name for name, value in _RELATIONSHIPS])
        self.relationshipBox.setAccessibleName("你们的关系")
        relation_label.setBuddy(self.relationshipBox)
        box.addWidget(self.relationshipBox)
        self.relEdit = LineEdit()
        self.relEdit.setPlaceholderText("例如：刚认识的朋友，正在慢慢熟悉")
        self.relEdit.setAccessibleName("自定义关系背景")
        box.addWidget(self.relEdit)
        self.relationshipBox.currentIndexChanged.connect(
            lambda index: self.relEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        )
        box.addWidget(self._hint("帮助助手把握称呼、语气和回应分寸。"))
        style_label = _label("说话风格（可选）", 13)
        box.addWidget(style_label)
        self.styleEdit = LineEdit()
        self.styleEdit.setPlaceholderText("例如：话少、不用标点、偶尔用 doge、不说客套话")
        self.styleEdit.setAccessibleName("说话风格")
        style_label.setBuddy(self.styleEdit)
        box.addWidget(self.styleEdit)
        box.addWidget(self._hint("生成和润色本来就照着你最近发的消息模仿；这里可以再补一句你自己的口吻。"))
        context_label = _label("参考上下文", 13)
        box.addWidget(context_label)
        self.contextBox = SpinBox()
        self.contextBox.setRange(3, 30)
        self.contextBox.setAccessibleName("参考的最近消息条数")
        context_label.setBuddy(self.contextBox)
        box.addWidget(self.contextBox)
        box.addWidget(self._hint(
            "生成和润色时看最近这么多条消息。太少会丢上下文，太多会稀释重点，建议 6–12。"
        ))
        target_row = QHBoxLayout()
        target_row.addWidget(_label("群聊指定回复对象", 13), 1)
        self.targetSwitch = SwitchButton()
        self.targetSwitch.setOnText("开")
        self.targetSwitch.setOffText("关")
        self.targetSwitch.setAccessibleName("群聊指定回复对象")
        target_row.addWidget(self.targetSwitch)
        box.addLayout(target_row)
        box.addWidget(self._hint(
            "开了以后群聊里可以选回复给谁，生成和润色都针对 TA，发送时可带 @。关了就正常回复。"
        ))
        dock_row = QHBoxLayout()
        dock_row.addWidget(_label("吸附在聊天窗口旁边", 13), 1)
        self.dockSwitch = SwitchButton()
        self.dockSwitch.setOnText("开")
        self.dockSwitch.setOffText("关")
        self.dockSwitch.setAccessibleName("吸附在聊天窗口旁边")
        self.dockSwitch.checkedChanged.connect(self._dock_switch_changed)  # 这个开关立刻生效
        dock_row.addWidget(self.dockSwitch)
        box.addLayout(dock_row)
        box.addWidget(self._hint("开着时聊天窗口一动，这个框就跟着贴在它右边（右边放不下翻到左边）。"))
        update_row = QHBoxLayout()
        update_row.addWidget(_label("启动时检查更新", 13), 1)
        self.updateSwitch = SwitchButton()
        self.updateSwitch.setOnText("开")
        self.updateSwitch.setOffText("关")
        self.updateSwitch.setAccessibleName("启动时检查更新")
        update_row.addWidget(self.updateSwitch)
        box.addLayout(update_row)
        box.addWidget(self._hint(
            "只向 GitHub 查最新版本号，不发送任何数据。国内访问 GitHub 慢的话关掉也行。"
        ))
        debug_row = QHBoxLayout()
        debug_row.addWidget(_label("调试视图", 13), 1)
        self.debugSwitch = SwitchButton()
        self.debugSwitch.setOnText("开")
        self.debugSwitch.setOffText("关")
        self.debugSwitch.setAccessibleName("调试视图")
        self.debugSwitch.checkedChanged.connect(self._debug_toggled)  # 这个开关立刻生效，不等「保存设置」
        debug_row.addWidget(self.debugSwitch)
        box.addLayout(debug_row)
        box.addWidget(self._hint(
            "另开一个窗口实时显示截到的画面和识别框：绿 = 我、蓝 = 对方、灰 = 过滤掉的灰字、"
            "红 = 当成图片丢掉、黄 = 小字丢掉。只在内存里画，不存图。"
        ))
        body.addWidget(preference)

        models = _Surface()
        box = QVBoxLayout(models)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_label("模型", 16, "#304c3c", True))
        self._fetched = _Fetched()
        self._fetched.done.connect(self._models_fetched)
        self.draft = self._model_group(box, "生成 / 润色 · 语言模型", providers.DRAFT_PROVIDERS)
        box.addWidget(self._hint(
            "写回复和润色都走这一家。OpenAI / Anthropic / Gemini 三种接口都走各自官方 SDK。"
            "默认 DeepSeek 官网直连，国内最快。"
        ))
        think_row = QHBoxLayout()
        think_row.addWidget(_label("开启思考模式", 13), 1)
        self.thinkingSwitch = SwitchButton()
        self.thinkingSwitch.setOnText("开")
        self.thinkingSwitch.setOffText("关")
        self.thinkingSwitch.setAccessibleName("开启思考模式")
        think_row.addWidget(self.thinkingSwitch)
        box.addLayout(think_row)
        box.addWidget(self._hint(
            "关：秒回，够用。开：模型先想再写，更斟酌但慢好几倍、贵一些。"
            "只有 " + " / ".join(providers.THINKING) + " 认这个开关。"
        ))
        body.addWidget(models)
        self.settingsFeedback = _label("", 13, _GREEN)
        self.settingsFeedback.hide()
        body.addWidget(self.settingsFeedback)
        actions = QHBoxLayout()
        back = PushButton("返回")
        back.clicked.connect(self._back_home)
        actions.addWidget(back)
        actions.addStretch(1)
        self.saveButton = PrimaryPushButton("保存设置")
        self.saveButton.clicked.connect(self._save)
        actions.addWidget(self.saveButton)
        body.addLayout(actions)
        body.addWidget(self._hint("保存后立刻用于下一次生成和润色。"))
        body.addStretch(1)
        self._load_settings()

    def _hint(self, text):
        """设置页字段下面的灰字说明：记下来，紧凑模式一起隐藏。"""
        label = _label(text, 12, _MUTED)
        self._hintLabels.append(label)
        return label

    def _model_group(self, box, title, table):
        """「来源 / 地址 / 密钥 / 模型」一组控件。table 是 core/providers.py 里那张表。"""
        group = SimpleNamespace(table=table, ids=list(table),
                                stored_key=lambda: settings.llm_key())
        heading = QHBoxLayout()
        heading.addWidget(_label(title, 14, "#304c3c", True), 1)
        group.keyState = _label("", 12, _GREEN)
        group.keyState.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        heading.addWidget(group.keyState)
        box.addLayout(heading)
        source_label = _label("来源", 13)
        box.addWidget(source_label)
        group.providerBox = ComboBox()
        group.providerBox.setMinimumWidth(0)  # 选项文字长短不一，别让它撑开设置页
        group.providerBox.addItems([table[i].name for i in group.ids])
        group.providerBox.setAccessibleName(f"{title} 来源")
        source_label.setBuddy(group.providerBox)
        box.addWidget(group.providerBox)
        # 地址行：只有「自定义」来源要自己填，别的来源这一行藏着
        group.baseLabel = _label("Base URL", 13)
        box.addWidget(group.baseLabel)
        group.baseEdit = LineEdit()
        group.baseEdit.setAccessibleName(f"{title} Base URL")
        group.baseLabel.setBuddy(group.baseEdit)
        box.addWidget(group.baseEdit)
        key_label = _label("密钥", 13)
        box.addWidget(key_label)
        group.keyEdit = PasswordLineEdit()
        group.keyEdit.setAccessibleName(f"{title} API 密钥")
        key_label.setBuddy(group.keyEdit)
        group.keyEdit.returnPressed.connect(self._save)
        box.addWidget(group.keyEdit)
        box.addWidget(self._hint("上面选哪家就填哪家的 key；换来源重填一次，只存这一把。"))
        model_label = _label("模型", 13)
        box.addWidget(model_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        group.modelBox = EditableComboBox()  # 能选也能手打，接口新出的模型不用等我改代码
        group.modelBox.setMinimumWidth(0)
        group.modelBox.setAccessibleName(f"{title} 模型")
        model_label.setBuddy(group.modelBox)
        row.addWidget(group.modelBox, 1)
        group.fetchButton = PushButton("获取模型")
        group.fetchButton.setAccessibleName(f"获取{title}的可用模型列表")
        group.fetchButton.clicked.connect(lambda: self._fetch_models(group))
        row.addWidget(group.fetchButton)
        box.addLayout(row)
        group.status = _label("", 12, _MUTED)
        box.addWidget(group.status)
        group.providerBox.currentIndexChanged.connect(lambda _: self._provider_changed(group))
        return group

    @staticmethod
    def _provider_of(group):
        return group.ids[max(0, group.providerBox.currentIndex())]

    def _provider_changed(self, group):
        """换来源：模型框回到这家该有的值（存的就是这家才用存的，否则用它的默认），地址清掉。"""
        provider = self._provider_of(group)
        stored = settings.draft_model()
        group.modelBox.clear()
        group.modelBox.setText(stored if provider == settings.draft_provider() else group.table[provider].default)
        group.baseEdit.setText("")
        group.status.setText("")
        self._sync_model_fields()

    def _sync_model_fields(self):
        """密钥已配置/未配置、占位文案、Base URL 行的显隐，
        外加紧凑模式下把来源按钮上的文字省略——ComboBox 是 QPushButton，
        minimumSizeHint 按整段文字算，不会自动换行/省略，长名字会把设置页撑宽。"""
        group = self.draft
        provider = self._provider_of(group)
        name = group.table[provider].name
        configured = bool(group.stored_key())
        group.keyState.setText("已配置" if configured else "未配置")
        group.keyEdit.setPlaceholderText("已配置，留空保留" if configured else f"输入 {name} API 密钥")
        if self._compact:
            name = group.providerBox.fontMetrics().elidedText(name, Qt.ElideRight, 180)
        group.providerBox.setText(name)
        custom = provider in providers.CUSTOM
        group.baseLabel.setVisible(custom)
        group.baseEdit.setVisible(custom)
        if custom:
            group.baseEdit.setPlaceholderText("https://你的服务/v1")

    def _fetch_models(self, group):
        """「获取模型」：拿填的 key（没填就拿存的）去问接口，网络调用丢后台线程。"""
        provider = self._provider_of(group)
        base = group.baseEdit.text().strip() if provider in providers.CUSTOM else None
        key = group.keyEdit.text().strip() or group.stored_key()
        if not key:
            group.status.setText("先填密钥")
            return
        if provider in providers.CUSTOM and not base:
            group.status.setText("先填 Base URL")
            return
        group.status.setText("获取中…")
        group.fetchButton.setEnabled(False)
        threading.Thread(target=lambda: self._list_models(group, provider, key, base),
                         daemon=True).start()

    def _list_models(self, group, provider, key, base):
        """后台线程：按协议走 llm；失败把原因一起送回主线程。"""
        try:
            spec = providers.DRAFT_PROVIDERS[provider]
            models = llm.list_models(spec.protocol, base or spec.base, key, headers=spec.headers)
            if spec.keep:  # 目录里混了别的协议时，只留这条路打得通的
                models = [m for m in models if spec.keep(m)]
            reason = "" if models else "这个来源没返回任何模型"
        except Exception as exc:  # 线程里漏异常会静默吞掉，按钮就永远停在禁用态
            models, reason = [], str(exc)[:120]
        self._fetched.done.emit(group, models, reason)

    def _models_fetched(self, group, models, reason):
        """回到主线程：填进下拉框，原来选中的还在列表里就留着。"""
        group.fetchButton.setEnabled(True)
        if not models:
            group.status.setText(reason or "获取失败，检查密钥、网络或 Base URL")
            return
        current = group.modelBox.text().strip()
        group.modelBox.clear()
        group.modelBox.addItems(models)
        if current in models:
            group.modelBox.setCurrentIndex(models.index(current))
        else:
            group.modelBox.setText(current)  # 手打的没在列表里也不清掉
        group.status.setText(f"共 {len(models)} 个")

    def _load_settings(self):
        relationship = settings.relationship()
        index = next((i for i, (_, value) in enumerate(_RELATIONSHIPS) if value == relationship),
                     len(_RELATIONSHIPS) - 1)
        self.relationshipBox.setCurrentIndex(index)
        self.relEdit.setText(relationship if _RELATIONSHIPS[index][1] is None else "")
        self.relEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        self.styleEdit.setText(settings.style())
        self.contextBox.setValue(settings.context())
        self.targetSwitch.setChecked(settings.reply_target())
        self.draft.providerBox.blockSignals(True)
        self.draft.providerBox.setCurrentIndex(self.draft.ids.index(settings.draft_provider()))
        self.draft.providerBox.blockSignals(False)
        self.draft.keyEdit.clear()
        self.draft.modelBox.clear()
        self.draft.modelBox.setText(settings.draft_model())
        self.draft.baseEdit.setText(settings.draft_base_url())
        self.draft.status.setText("")
        self.thinkingSwitch.setChecked(settings.thinking())
        self.updateSwitch.setChecked(settings.check_update())
        self.dockSwitch.blockSignals(True)
        self.dockSwitch.setChecked(self._docked)
        self.dockSwitch.blockSignals(False)
        self.set_debug_switch(settings.debug_view())  # 屏蔽信号地拨，别在加载时开关一遍窗口
        self._sync_model_fields()  # 上面屏蔽了信号，这里补一次
        self.settingsFeedback.hide()

    def _save(self):
        relationship = _RELATIONSHIPS[self.relationshipBox.currentIndex()][1]
        relationship = relationship or self.relEdit.text().strip()
        provider = self._provider_of(self.draft)
        base = self.draft.baseEdit.text().strip() if provider in providers.CUSTOM else ""
        if not relationship:
            self._settings_feedback("请填写关系背景，或选择一个已有选项。", error=True)
            self.relEdit.setFocus()
            return
        if provider in providers.CUSTOM and not base:
            self._settings_feedback("自定义来源要填 Base URL。", error=True)
            self.draft.baseEdit.setFocus()
            return
        name = self.draft.table[provider].name
        if not self.draft.keyEdit.text().strip() and not self.draft.stored_key():
            self._settings_feedback(f"请先填写 {name} 的 API 密钥。", error=True)
            self.draft.keyEdit.setFocus()
            return
        if not self.draft.modelBox.text().strip():
            self._settings_feedback(f"{name} 请先获取并选择一个模型。", error=True)
            self.draft.modelBox.setFocus()
            return
        try:
            settings.save(relationship, self.contextBox.value(),
                          draft_provider_text=provider,
                          llm_key_text=self.draft.keyEdit.text().strip() or None,
                          draft_model_text=self.draft.modelBox.text().strip(),
                          draft_base_url_text=base,
                          reply_target_on=self.targetSwitch.isChecked(),
                          style_text=self.styleEdit.text().strip(),
                          thinking_on=self.thinkingSwitch.isChecked(),
                          check_update_on=self.updateSwitch.isChecked(),
                          dock_on=self.dockSwitch.isChecked())
        except Exception:
            self._settings_feedback("保存失败，请检查配置文件是否可写后重试。", error=True)
            return
        self._set_docked(self.dockSwitch.isChecked(), save=False)  # dock_on 上面已经写进 settings.save 了
        self._load_settings()
        self._render_targets()  # 开关刚改过，回到首页时这一行该显该藏得重算一次
        self._settings_feedback("设置已保存，马上生效。")
        self._idle_status()

    def _debug_toggled(self, on):
        """调试视图独立于「保存设置」：拨一下就开窗/收窗，顺手落盘，重启还在。"""
        settings.save(debug_view_on=on)
        if self.on_toggle_debug:
            self.on_toggle_debug(on)

    def set_debug_switch(self, on):
        """调试窗被用户直接关掉时把开关拨回去；屏蔽信号，免得又回调一圈。"""
        self.debugSwitch.blockSignals(True)
        self.debugSwitch.setChecked(on)
        self.debugSwitch.blockSignals(False)

    def _settings_feedback(self, text, error=False):
        color = "#b44832" if error else _GREEN
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.settingsFeedback, qss, qss)
        self.settingsFeedback.setText(text)
        self.settingsFeedback.show()

    def open_settings(self):
        if self.pages.currentWidget() != self.settingsPage:
            self._load_settings()
        self.pages.setCurrentWidget(self.settingsPage)
        self.composer.hide()  # 设置页不需要输入框，别挤着看
        self.settingsButton.setEnabled(False)
        self._grow_for_settings()  # 首页收得很矮，进设置页要放开一点，不然表单挤在一条缝里
        (self.relationshipBox if settings.has_key() else self.draft.keyEdit).setFocus()

    def _grow_for_settings(self):
        """设置页：把窗口放到 _SETTINGS_H（屏幕放不下就按屏幕），只长不缩。"""
        work = self._work_area()
        self.win.resize(self.win.width(), max(self.win.height(), min(_SETTINGS_H, work.height())))
        self._realign()  # 长高了，吸附时下沿要重新对齐

    def _back_home(self):
        self.draft.keyEdit.clear()
        self.pages.setCurrentWidget(self.home)
        self.composer.show()
        self.settingsButton.setEnabled(True)
        self._apply_height()  # 回首页：收回内容高（设置页那会儿是不动高度的）

    # ------------------------------------------------------------ 生成/润色/发送

    def _draft_text(self):
        return self.input.toPlainText().strip()

    def _render_buttons(self):
        """按钮上的字 + 提示：两条发送路径的差别要写在按钮上，别让人猜点下去会不会多一次调用。"""
        self.sendButton.setText("发送")
        self.sendButton.setToolTip(
            "粘进微信输入框并按一次回车，一个字都不改；也可以按 Ctrl+回车。\n"
            "想发之前先改一遍：点「润色并发送」，或者按 Ctrl+Shift+回车。")
        self.sendPolishButton.setToolTip(
            "先把输入框里这段话润一遍（修错别字、按关系调口气、接上上下文）再发出去，"
            "跟点「发送」只差这一次润色；也可以按 Ctrl+Shift+回车。\n"
            "意思、态度、信息量不会变，原文留在「还原」里。")

    def _render_footer(self):
        self.footerLabel.setText(f"手动生成 · 发送前请过目 · v{VERSION}")

    def _can_act(self):
        """浏览别的会话时不让生成/发送——上下文和发送目标都是另一个会话的，容易串。"""
        return not self._busy and (not self._chat or self._shown == self._chat)

    def _keep_undo(self):
        """每次覆盖输入框前留下一份，用户点错了能还原。
        正在等「润色并发送」时跳过：这段原文马上会被润色结果覆盖，留着只会在备选行里
        多出一个跟输入框差不多的按钮，点了等于没换。"""
        if self._pending_send is not None:
            return
        old = self.input.toPlainText()
        if old.strip():
            self._undo = old
        self._render_alts()

    def _set_box(self, text):
        """写输入框。屏蔽信号：程序填的内容不算用户编辑。填完立刻重算按钮状态。"""
        self.input.blockSignals(True)
        self.input.setPlainText(text)
        self.input.blockSignals(False)
        self._refresh_buttons()

    def _generate(self):
        if not self._can_act():
            return
        if not settings.has_key():
            self.set_status("请先在设置里配置模型", "warning")
            self.open_settings()
            return
        if self._draft_text():
            self._keep_undo()  # 自己写了一半再点生成，原文留在「还原」里
        self.set_busy(True, "正在结合上下文写回复…")
        self.on_generate(self._shown)

    def _send_with_polish(self):
        """「润色并发送」/ Ctrl+Shift+回车：润一遍，回来直接发。只这一次，不改别的。"""
        if self._start_polish_send():
            self.on_polish_and_send(self._pending_send, self._shown)

    def _polish(self):
        if not self._can_act():
            return
        text = self._draft_text()
        if not text:
            self.set_status("先在输入框里写你要发的话，再点润色", "warning")
            self.input.setFocus()
            return
        if not settings.has_key():
            self.set_status("请先在设置里配置模型", "warning")
            self.open_settings()
            return
        self._keep_undo()
        self.set_busy(True, "正在润色，让它接得上对话…")
        self.on_polish(text, self._shown)

    def _start_polish_send(self):
        """润色并发送的公共前半段：查状态、留一份原文，返回要不要真的去润。"""
        if not self._can_act():
            return False
        text = self._draft_text()
        if not text:
            self.set_status("先写点什么再发", "warning")
            self.input.setFocus()
            return False
        if not settings.has_key():
            # 没配模型：不能替用户静默改掉一次发送的语义（说好润色结果直发却发了原文）。
            # 退成原样直发，并在状态行说清楚为什么，别让消息卡在输入框里出不去。
            self.set_status("没配模型，这条直接原样发出去了；想让它先润色，先去设置里配一把 key", "warning")
            self.on_send(text, self._shown)
            return False
        self._keep_undo()
        self._pending_send = text
        self.set_busy(True, "正在润色，改好就直接发出去…")
        return True

    def _send(self):
        if not self._can_act():
            return
        text = self._draft_text()
        if not text:
            self.set_status("先写点什么再发", "warning")
            self.input.setFocus()
            return
        self.on_send(text, self._shown)

    def _on_text_changed(self):
        """输入框里有没有内容决定润色/发送亮不亮。"""
        self._refresh_buttons()

    def sent(self, ok, message):
        """父进程回报发送结果：真发出去了才清空输入框。
        发送失败（不管走的是哪条路）输入框里的东西都留着，用户不用重打。"""
        self._pending_send = None
        self.set_status(message, "success" if ok else "error")
        if ok:
            self._set_box("")
            self.alts = []
            self._undo = None
            self._render_alts()
        self._refresh_buttons()

    def set_replies(self, candidates):
        """生成好了：第 1 条进输入框，另外两条挂成「换一条」。"""
        self.set_busy(False)
        candidates = [c for c in (candidates or []) if str(c).strip()]
        if not candidates:
            self.set_status("没生成出可用的回复，再点一次「生成回复」试试。", "error")
            return
        self._keep_undo()
        self._set_box(candidates[0])
        self.alts = list(candidates[1:3])
        self._render_alts()
        tail = f"，下面还有 {len(self.alts)} 条备选" if self.alts else "，不满意就再点一次"
        self.set_status("写好了" + tail + "；改完点发送。", "success")

    def set_polished(self, text):
        """润色好了：替换输入框内容，原文留在「还原」里。"""
        self.set_busy(False)
        if not str(text).strip():
            self.set_status("润色没返回内容，再试一次。", "error")
            return
        self._keep_undo()
        self._set_box(text)
        self._render_alts()
        self.set_status("润色好了，看看顺不顺口；不满意点「还原」", "success")

    def send_polished(self, text):
        """「润色并发送」的第二步：润好的这段直接交给父进程发出去。
        原样留在「还原」里；真发出去了才会清空输入框（见 sent()）。"""
        self.set_busy(False)
        if not str(text).strip():
            self._pending_send = None
            self.set_status("润色没返回内容，没发出去；再点一次「润色并发送」试试。", "error")
            return
        self._keep_undo()
        self._set_box(text)
        self._render_alts()
        self.set_status("润色好了，正在发出去…", "busy")
        self.on_send(str(text).strip(), self._shown)

    def polish_failed(self, message, sending=False):
        """润色这一步失败：一个字都没发出去。原文还在输入框里，随时能直接发。"""
        self.set_busy(False)
        if sending:
            self._pending_send = None
            self.set_status(f"{message}（没发出去，可以直接点「发送」原样发）", "error")
        else:
            self.set_status(message, "error")

    def cancel_pending_send(self, message):
        """结果对不上当前会话了：把「等着发」的状态清掉，别让它悬在那。"""
        self._pending_send = None
        self.set_status(message, "warning")

    def _render_alts(self):
        """备选按钮 + 「还原」：都是当前可用状态下才出现。"""
        for button in self._altButtons:
            self.altLayout.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._altButtons = []
        for index, text in enumerate(self.alts):
            button = PushButton(_short(text, 10 if self._compact else 12), self.altRow)
            button.setToolTip(text)
            button.setAccessibleName(f"换成备选 {index + 1}：{text}")
            button.clicked.connect(lambda _=False, i=index: self._swap(i))
            self.altLayout.addWidget(button)
            self._altButtons.append(button)
        if self._undo is not None:
            undo = PushButton("还原", self.altRow)
            undo.setToolTip("换回上一次的内容（你自己写的那段）")
            undo.setAccessibleName("还原上一次的内容")
            undo.clicked.connect(self._restore)
            self.altLayout.addWidget(undo)
            self._altButtons.append(undo)
        self.altRow.setVisible(bool(self._altButtons))
        self._apply_height()  # 备选那一行出来/收起来，窗口高矮跟着走

    def _swap(self, index):
        """点备选：跟输入框里那条对调，来回点着挑。"""
        if not self._can_act() or index >= len(self.alts):
            return
        old = self.input.toPlainText()
        text, self.alts[index] = self.alts[index], old
        self._set_box(text)
        self._render_alts()
        self._refresh_buttons()

    def _restore(self):
        if self._undo is None:
            return
        old = self.input.toPlainText()
        self._set_box(self._undo)
        self._undo = old if old.strip() else None
        self._render_alts()
        self._refresh_buttons()
        self.set_status("已还原", "idle")

    # ---------------------------------------------------------------- 会话跟随

    def current_chat(self):
        """界面上正在看的会话（不一定是微信当前开着的那个）。"""
        return self._shown

    def set_chat(self, title):
        """微信切到了哪个会话：登记进下拉框并自动跟过去，不触发用户选择的回调。"""
        if not title:
            return
        browsing = self._shown != self._chat  # 正看着的就是它、但之前是「浏览中」：也得重画
        self._chat = title
        self._add_chat(title)
        if title != self._shown or browsing:
            self.chatBox.blockSignals(True)
            self.chatBox.setCurrentIndex(self.chatBox.findText(title))
            self.chatBox.blockSignals(False)
            self._switch_to(title)
        self._follow_text()
        self._refresh_buttons()

    def _add_chat(self, title):
        """新会话自动进下拉框；addItem 添第一条时会自己选中，别让它触发切换。"""
        if not title or self.chatBox.findText(title) >= 0:
            return
        self.chatBox.blockSignals(True)
        self.chatBox.addItem(title)
        self.chatBox.blockSignals(False)

    def _on_chat_selected(self, index):
        """用户自己挑了一个会话：只换看的内容，微信那边不动。"""
        title = self.chatBox.itemText(index)
        if title and title != self._shown:
            self._switch_to(title)

    def _switch_to(self, title):
        """换正在看的会话：记录、对方最近说、条数一起换过去。输入框是共用的，不跟着换。"""
        self._shown = title
        self.feed.clear()
        for line in self.feeds.get(title, []):
            self.feed.appendPlainText(line)
        her = self.hers.get(title)
        if her:
            self._show_latest(her)
        else:
            self.latest.setText("")
            self._render_latest()
        self._render_count()
        self._follow_text()
        self._render_targets()
        if self._chat and self._shown != self._chat:
            self.set_status(f"正在浏览「{self._shown}」，回微信切到这个会话才能生成和发送。", "warning")
        else:
            self._idle_status()

    def set_targets(self, chat, senders, current):
        """某个会话的发言人名单（最近的在前）和当前回复对象；正看着它才重画。"""
        self.targets[chat] = (list(senders), current)
        if chat == self._shown:
            self._render_targets()

    def _render_targets(self):
        """开关关着、或这个会话没有发言人（单聊），这一行就不出现。
        重填下拉框时屏蔽信号，别把自己的填充当成用户挑的。"""
        senders, current = self.targets.get(self._shown, ([], None))
        visible = bool(senders) and settings.reply_target()
        self.targetRow.setVisible(visible)
        self._apply_height()  # 群聊多出「回复对象」这一行，窗口得跟着长
        if not visible:
            return
        self.targetBox.blockSignals(True)
        self.targetBox.clear()
        self.targetBox.addItems(senders)
        self.targetBox.setCurrentIndex(senders.index(current) if current in senders else 0)
        self.targetBox.blockSignals(False)

    def _on_target_selected(self, index):
        """用户挑了回复对象：这个会话的生成/润色都按 TA 来。"""
        name = self.targetBox.itemText(index)
        if not name:
            return
        senders, _ = self.targets.get(self._shown, ([], None))
        self.targets[self._shown] = (senders, name)
        self.set_status(f"回复对象改成「{name}」，点生成时按 TA 写", "idle")
        if self.on_target_change:
            self.on_target_change(self._shown, name)

    def at_prefix_enabled(self):
        """发送时要不要带「@名字 」前缀（只记在界面上，不落盘）。"""
        return self.atCheck.isChecked()

    def auto_generate(self):
        """「自动生成回复」开着没有：main.py 收到对方新消息时问这个。

        只影响「要不要自动起草」，发送永远等用户按「发送」——这条没有开关。
        开关只记在界面上、不落盘：重启回到「关」，免得哪天静悄悄开始自动调模型。
        """
        return self.autoSwitch.isChecked()

    def has_draft(self):
        """输入框里有没有东西：有（用户自己在写，或已经躺着一版草稿）就别自动覆盖。"""
        return bool(self._draft_text())

    def _auto_toggled(self, on):
        """用户自己拨的：把当前这个状态说清楚，别让人以为会连发送也一起自动了。"""
        if on:
            self.set_status("已开自动生成：对方发来新消息就自动起草一版，发送还是要你自己按", "idle")
        else:
            self._idle_status()

    def _follow_text(self):
        self.chatFollow.setText(("跟随" if self._shown == self._chat else "浏览中") if self._chat else "")

    # ---------------------------------------------------------------- 吸附

    def docked(self):
        return self._docked

    def _dock_toggled(self, on):
        """用户自己拨的（标题栏图钉或设置页开关）：这个才算数，落盘。"""
        self._set_docked(on, save=True)
        self.set_status("已吸附在聊天窗口旁边" if self._docked else "已脱离，位置自己摆", "idle")

    def _dock_switch_changed(self, on):
        """设置页里那个开关：跟标题栏图钉是一回事。"""
        self._set_docked(on, save=True)

    def _undock(self):
        """拖动悬浮框 = 这次不想贴着微信：只脱开这一会儿，不动存下来的设置——
        标题栏太容易被误碰，一次手抖不该把「吸附」永久关掉。要改设置就拨图钉或设置页开关（那个会存）。"""
        if not self._docked:
            return
        self._set_docked(False, save=False)
        self.set_status("已脱离吸附，点标题栏的图钉可以吸回来", "idle")

    def _set_docked(self, on, save):
        self._docked = bool(on)
        self.dockButton.setChecked(self._docked)
        self.dockSwitch.blockSignals(True)
        self.dockSwitch.setChecked(self._docked)
        self.dockSwitch.blockSignals(False)
        if save:
            settings.save(dock_on=self._docked)
        if self._docked:
            self._rect = None  # 强制下一帧重新贴一次，哪怕窗口一直没动

    def dock_to(self, rect):
        """rect = 聊天窗口的 (left, top, right, bottom)，物理像素；吸附开着才动。

        窗口没动就不折腾；右边放不下翻到左边；**下沿跟聊天窗口的下沿对齐**（微信的输入框也贴底，
        这样两边的输入区在同一水平线上）；高度按内容给，屏幕放不下才夹。
        """
        if not self._docked or not rect or rect == self._rect:
            return
        area = self._logical(rect)
        if area is None:
            return
        self._rect = rect
        screen = QGuiApplication.screenAt(area.center()) or self.app.primaryScreen()
        work = screen.availableGeometry()
        width = min(self.win.width(), max(320, work.width() // 2))
        height = self._dock_height(work)
        x = area.right() + _CHAT_GAP
        if x + width > work.right():
            x = area.left() - width - _CHAT_GAP  # 右边放不下就翻到左边
        x = max(work.left(), min(x, work.right() - width))
        y = area.bottom() - height + 1  # QRect.bottom() 是闭区间，+1 才是下沿对齐
        y = max(work.top(), min(y, work.bottom() - height + 1))
        self.win.resize(width, height)
        self.win.move(x, y)

    def _dock_height(self, work):
        """吸附时的高度：跟没吸附一个口径，都是内容多高就多高，屏幕装不下才夹。
        设置页是长表单，别让首页那个矮高把它顶回去。"""
        if self.pages.currentWidget() is not self.home:
            return min(_SETTINGS_H, work.height())
        return min(self._desired_height(), work.height())

    def _logical(self, rect):
        """Win32 报的是物理像素，Qt 摆窗口用的是逻辑像素：按那块屏的比例换一下。"""
        left, top, right, bottom = rect
        for screen in QGuiApplication.screens():
            scale = screen.devicePixelRatio() or 1.0
            geo = screen.geometry()
            phys = QRect(round(geo.x() * scale), round(geo.y() * scale),
                         round(geo.width() * scale), round(geo.height() * scale))
            if phys.contains(left, top) or phys.contains(right, bottom):
                return QRect(round((left - phys.x()) / scale) + geo.x(),
                             round((top - phys.y()) / scale) + geo.y(),
                             round((right - left) / scale), round((bottom - top) / scale))
        return None

    # ---------------------------------------------------------------- 状态与记录

    def set_update(self, latest, url):
        """后台线程查到比当前新的版本才会调这个。只显示版本号和 Release 链接，别的什么都没有。"""
        self.updateLabel.setText(f"有新版本 v{latest}")
        self.updateLink.setUrl(url)
        self.updateBar.show()
        self._apply_height()  # 多出来一行提示，收着的时候窗口跟着长高一点（tick 在主线程里调）

    def _close_update(self):
        """关掉更新提示：窗口跟着缩回去。"""
        self.updateBar.hide()
        self._apply_height()

    def set_capture(self, on, reason=""):
        """父进程回报的状态：只改界面，不回调（不然和父进程来回打架）。reason 为空用默认说明。"""
        self.captureSwitch.blockSignals(True)
        self.captureSwitch.setChecked(on)
        self.captureSwitch.blockSignals(False)
        self._apply_capture_text(on, reason)

    def _capture_toggled(self, on):
        """用户自己拨的开关：界面先改，再通知父进程去开/停采集。"""
        self._apply_capture_text(on)
        if self.on_toggle_capture:
            self.on_toggle_capture(on)

    def _apply_capture_text(self, on, reason=""):
        """开关状态对应的状态行；正在生成时不覆盖进度提示。"""
        if self._busy:
            return
        if not on:
            self.set_status(reason or "采集已暂停，聊天内容不再读取", "warning")
        else:
            self._idle_status()

    def _idle_status(self):
        if self._busy:  # 正在生成/润色时别把进度提示盖掉
            return
        if not settings.has_key():
            self.set_status("请先在设置中配置模型", "warning")
        elif not self.captureSwitch.isChecked():
            self.set_status("采集已暂停，聊天内容不再读取", "warning")
        else:
            self.set_status("写点什么，或者点「生成回复」", "idle")

    def set_busy(self, busy, what=""):
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.progress.start()
            self.set_status(what or "正在处理…", "busy")
        else:
            self.progress.stop()
            self._apply_height()  # 进度条收掉了：这条路径不会走 set_status，自己补一次
        self._refresh_buttons()

    def _refresh_buttons(self):
        """忙碌、浏览别的会话、输入框空 —— 四个按钮各自该不该亮。"""
        can = self._can_act()
        has_text = bool(self._draft_text())
        self.generateButton.setEnabled(can)
        self.polishButton.setEnabled(can and has_text)
        self.sendButton.setEnabled(can and has_text)
        self.sendPolishButton.setEnabled(can and has_text)

    def set_status(self, text, kind="idle"):
        colors = {"idle": _MUTED, "busy": _GREEN, "success": _GREEN,
                  "warning": "#93611d", "error": "#b44832"}
        markers = {"idle": "●", "busy": "●", "success": "✓", "warning": "!", "error": "!"}
        qss = f"BodyLabel {{ color: {colors.get(kind, _MUTED)}; background: transparent; }}"
        setCustomStyleSheet(self.status, qss, qss)
        self.status.setText(f"{markers.get(kind, '●')}  {text}")
        self._apply_height()  # 状态行换了句话可能就多一行，窗口得跟着

    def _toggle_details(self):
        """会话详情整块的开关：默认收着，点会话标题后面那个箭头才铺开下级几节。"""
        if self._details_open:
            self._expanded_h = max(self._expanded_h or 0, self.win.height())  # 记下铺开时那个高
        self._details_open = not self._details_open
        self._render_details()
        self._apply_height()  # 收着就把窗口一起缩掉，别留一大片空

    def _toggle_history(self):
        self._feed_open = not self._feed_open
        self._render_history()
        self._apply_height()  # 记录区铺开/收起，窗口跟着长高/缩回去

    def _render_details(self):
        """会话详情整块的显隐：收着就只剩上面那一行会话标题（+ 条数），铺开才有下级几节。"""
        expanded = self._details_open
        self.detailsButton.setIcon(FIF.CHEVRON_DOWN_MED if expanded else FIF.CHEVRON_RIGHT_MED)
        tip = "收起会话详情" if expanded else "展开会话详情：对方最近说、聊天记录"
        self.detailsButton.setToolTip(tip)
        self.detailsButton.setAccessibleName(tip)
        self.historyButton.setVisible(expanded)
        self._render_latest()
        self._render_history()

    def _render_latest(self):
        """「对方最近说」：详情收着时、或这个会话还没有对方的消息，都不露头。"""
        self.context.setVisible(self._details_open and bool(self.latest.text()))

    def _render_history(self):
        """聊天记录比详情还低一级：详情收着时它也跟着藏，别自己单独冒出来。"""
        self.feed.setVisible(self._details_open and self._feed_open)
        self.historyButton.setText(("收起" if self._feed_open else "展开") + "聊天记录")

    def _render_count(self):
        """条数挂在会话标题后面，收着的时候也能看出这个会话记了多少条。"""
        count = self.counts.get(self._shown, 0)
        self.countLabel.setText(f"{count} 条" if count else "")
        self.countLabel.setToolTip(f"这个会话已经记下 {count} 条消息" if count else "")
        self.countLabel.setVisible(bool(count))

    def log(self, line):
        """采集状态行：只进正在看的那个会话，不按会话存。"""
        bar = self.feed.verticalScrollBar()
        follow = self.feed.isHidden() or bar.value() >= bar.maximum() - 4
        self.feed.appendPlainText(line)
        if follow:
            bar.setValue(bar.maximum())

    def log_message(self, who, text, name="", timestamp=None, chat=None):
        """按会话存一份；只有正在看的那个会往显示区里写。"""
        chat = chat or self._shown
        speaker = (name or "对方") if who == "her" else "我"
        timestamp = timestamp or datetime.now().strftime("%H:%M")
        self.counts[chat] = self.counts.get(chat, 0) + 1
        lines = self.feeds.setdefault(chat, [])
        lines.append(f"{timestamp}  {speaker}\n{text}\n")
        del lines[:-_LOG_LINES]
        if who == "her":
            self.hers[chat] = text
        self._add_chat(chat)
        if chat != self._shown:
            return
        self.log(lines[-1])
        if who == "her":
            self._show_latest(text)
        self._render_count()

    def _show_latest(self, text):
        self.latest.setText(text if len(text) <= 120 else text[:120] + "…")
        self.latest.setToolTip(text)
        self._render_latest()

    def after(self, ms, fn):
        QTimer.singleShot(ms, fn)

    def run(self):
        self.app.exec()
