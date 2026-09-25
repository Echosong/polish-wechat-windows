# polish-chat — 接续说明

仓库 `Echosong/polish-wechat-windows`，**界面上显示的名字是「润色」**（标题栏、窗口标题都用这两个汉字；
`polish-chat` 只用在文件名、exe、发布包和仓库名上）。这份文档给接手改代码的人看：架构、硬约束、以及为什么这么选。

> 前身是 `jev-chat-windows`（作者自己维护的 Windows 版，内置 Jev 判断内核 + 三段式自动分析）。
> 重做成 polish-chat 时把判断内核整条链路删了，改成手动触发的一次生成 / 润色，key 从两把收敛成一把。
> 采集层（截图 + OCR）沿用前身，出处见 `NOTICE`。

个人用的聊天输入辅助工具，挂在自己电脑的微信旁边：读屏上的对话当上下文，**你想回的时候点一下**
「生成回复」让它写一版、或者自己写好点「润色」改顺，最后点「发送」发出去。
发不发全由你按按钮决定——程序没有任何自动发送路径。调不调模型默认也是手动；唯一的例外是输入区那个
「自动生成」开关（默认关），开了以后收到对方新消息会自动起草一版，仅此而已。

## 硬约束（照做，别破）

1. 只读**自己设备上、自己有权查看**的对话。
2. 采集只用**窗口级截图 + 本地离线 OCR**。不 hook、不注入、不读微信数据库、不解密、不碰微信进程。
3. **截图不落盘**：捕获得到的位图始终是内存里的对象（numpy），全程不写磁盘、不进日志、不上传。
4. **不发新消息、不轮询**：对方来消息只记账（进上下文和聊天记录），默认不触发任何模型调用。
   唯一会按回车的地方是用户点了「发送」或「润色并发送」之后的 `app/fill.py: send()`，
   **没有开关能绕过这一条**。草稿默认也是手动的；输入区那个「自动生成」开关（默认关、不落盘）打开后，
   收到对方新消息会自动起草一版填进输入框——这只影响「什么时候调模型」，不影响「什么时候发」。
   「润色并发送」（按钮或 `Ctrl+Shift+回车`）多出来的也只有**一次润色调用**：润完由 `main.py` 的
   tick 直接把结果交给 `send()`，发送动作仍然由用户那一次点击/按键触发；润色失败就一个字都不发。
5. **不碰钱**：转账、红包、收款相关界面元素一律不碰。
6. 全局只有**一把** key：语言模型那把（`LLM_API_KEY`），只从环境变量 / 注册表读，任何文件不出现 key。
7. Python 读写文件一律 `encoding='utf-8'`。

## 为什么走 OCR（已实测的结论，别重测）

- 目标窗口界面自绘在一块 GPU 合成画布上（`MMUIRenderSubWindowHW`）。UIA 树只有 2 个节点、**没有控件树**——实测证伪。
- 所以唯一干净的非侵入采集路 = 截自己的微信窗口 + 本地 OCR。离线、零 token。

## 链路

```
WGC 截聊天窗口 → 像素锚点定位消息区 → OCR 会话名和消息（按气泡颜色分 me / her）
  → 进度按会话分别记账（上下文、聊天记录、群发言人）——不出网
  → 用户点「生成回复」：core.engine.generate()  一次模型调用 → 3 条候选，第 1 条进输入框，另两条挂成备选
    （开了「自动生成」开关时这一步由 main.py 的 drain() 在收到对方新消息且输入框为空时代劳，其余完全一样）
  → 用户自己写 + 点「润色」：core.engine.polish() 一次模型调用 → 原句改顺（不改意思）
  → 用户点「润色并发送」（或按 Ctrl+Shift+回车）：同上先润一次 → 润好了不等第二次点击，
    直接把结果交给发送那一步；润色失败就一个字都不发，原文留在输入框里
  → 用户点「发送」：写剪贴板 → 聚焦微信输入框 → Ctrl+V → 回车（原样发，不过模型）
```

## 已经建好，直接用

| 文件 | 作用 |
|---|---|
| `core/providers.py` | 语言模型来源表（12 家，OpenAI / Anthropic / Gemini 三种协议），纯数据 |
| `core/keys.py` | key 读取（环境变量 → 注册表）、报错脱敏、`ChatError` |
| `core/llm.py` | 三种协议的薄适配层：`chat()` / `list_models()`，一律走官方 SDK |
| `core/draft.py` | 两条提示词链路：`draft_candidates()` 写 3 条候选、`polish_text()` 润色用户草稿；解析器已自测 |
| `core/engine.py` | **唯一入口**：`generate(messages, relationship, ...)` / `polish(text, messages, ...)` |
| `app/capture.py` | 找微信窗口、WGC 采集、像素锚点定位消息区、`window_rect()`（悬浮框吸附用） |
| `app/ocr.py` | RapidOCR 内存内识别 + 按气泡颜色分说话人 + 帧间去重 |
| `app/fill.py` | `fill()` 只粘贴；`send()` 粘贴 + 回车（只由「发送」按钮触发） |
| `app/overlay.py` | 悬浮框：输入框 + 生成回复 / 润色 / 发送 + 备选 + 独立设置页；自动吸附在聊天窗口旁边 |
| `main.py` | 父进程：界面 + 网络调用；子进程（`app/worker.py`）负责采集和 OCR |
| `tools/demo.py` | 端到端冒烟（需 key + 联网）：`python tools/demo.py` |
| `tools/preview_ui.py` | 合成数据预览界面并截图，不采集、不联网：`--state ready --screenshot x.png` |

`messages` 形如 `[("her","中文"),("me","中文")]`（群聊第三项是发言人名），最新一条在最后。
引擎完全不关心消息怎么来的——OCR 把屏幕上的对话整理成这个 list 喂进 `generate()` 即可。

## 技术坑备忘

- GPU 窗口截图黑屏 → 用 Windows Graphics Capture，别用普通 BitBlt/PrintWindow。
- 图片全程内存对象（numpy），OCR 引擎吃数组不吃路径，天然不落盘。
- 一帧 OCR 250~800ms，必须放子进程，否则 Qt 主线程僵住。
- 应用要跑必须放开沙箱：`multiprocessing.Queue()` 建命名管道，受限模式 `WinError 5`。
- Win32 给的是物理像素、Qt 摆窗口用逻辑像素：`Overlay._logical()` 按屏幕 devicePixelRatio 折算
  （本机两块 1920×1080、dpr=1.0，折算退化成恒等）。

## 参考项目

安卓原版 `Finderchangchang/jev-chat-JARVIS`。**判断内核（Jev / TypeSafe System One / 百炼
decision-model-preview）在本 Windows 版里已经砍掉**：那套三段式（判断 → 起草 → 排序）只在
用户手动点按钮的场合才划算，而这个版本要的是「想回的时候回一句」，一次调用够了。
旧代码在 git 历史里（`core/jev_client.py`、`core/questions.py`）。
