# -*- coding: utf-8 -*-
"""端到端冒烟：把一段真实对话喂给 core.engine，看「生成回复」和「润色」分别出来什么。
要一把 LLM_API_KEY、要联网（GUI 上那两条链路就是这两个函数）。

    set LLM_API_KEY=...        (Windows)
    export LLM_API_KEY=...     (mac/Linux)
    python tools/demo.py                        # 生成 3 条候选 + 润色一段示例草稿
    python tools/demo.py --draft "我自己写的"     # 只跑润色，看它怎么改
    python tools/demo.py --provider openai --model gpt-5-mini

可选来源见 core/providers.py 的 DRAFT_PROVIDERS（默认 deepseek 官网直连）。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

# 直接 `python tools/demo.py` 跑时，sys.path[0] 是 tools/，仓库根不在里面
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core.keys import ChatError  # noqa: E402
from core.engine import generate, polish  # noqa: E402
from app import settings  # noqa: E402

RELATIONSHIP = "romantic partners"
# (谁说的, 内容)；群聊再跟一个发言人名。最新一条在最后。
MESSAGES = [
    ("her", "你到家了吗"),
    ("me", "刚到，正准备洗澡"),
    ("her", "嗯……你今天是不是有点敷衍我"),
    ("her", "算了，不说了，当我没说"),
]
DRAFT = "没有敷衍你 刚刚在忙 现在好好聊"


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一遍生成和润色，看输出")
    parser.add_argument("--provider", default="deepseek", help="来源 id（见 core/providers.py）")
    parser.add_argument("--model", default="", help="模型 id；空 = 用该来源的默认模型")
    parser.add_argument("--base-url", default="", help="自定义来源才要；空 = 用表里的地址")
    parser.add_argument("--draft", default="", help="要润色的草稿；给了就只跑润色")
    parser.add_argument("--context", type=int, default=10, help="看最近多少条消息")
    args = parser.parse_args()

    draft = args.draft or DRAFT
    common = dict(provider=args.provider, model=args.model or None,
                  base_url=args.base_url or None, context=args.context)
    # core 那边只认环境变量，key 存在注册表里的话得先让 settings 把它搬进环境
    # （GUI 走的是同一条路：启动时问一次「配没配好」，顺手就搬了）
    settings.llm_key()
    print(f"来源 {args.provider} / 模型 {args.model or '默认'}\n")
    try:
        if not args.draft:  # 没指定草稿就两条链路都跑
            print("== 生成回复（第 1 条是模型最推荐的）==")
            for i, text in enumerate(generate(MESSAGES, RELATIONSHIP, **common), 1):
                print(f"  {i}. {text}")
        print("\n== 润色（只改怎么说，不改说什么）==")
        print(f"  原稿: {draft}")
        print(f"  改后: {polish(draft, MESSAGES, RELATIONSHIP, **common)}")
    except ChatError as e:
        print(f"\n失败: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
