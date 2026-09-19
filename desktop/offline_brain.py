"""离线微脑：无 API Key、不依赖大模型时的本地对话脑。

它不会"百科问答"，但它是诚实的 PASM 本地表达层：
- 记得自己是谁、是什么性格、处在什么成长阶段
- 记得"关于你"的事（notes），能答"你还记得吗"
- 能描述自己此刻的心情（来自真实情绪向量）
- 有礼貌、有温度，会反问延续对话
配合 settings 里的一键本地模型(ollama/llama-server)即升级为真 LLM。
"""
from __future__ import annotations

import random
import re

MOOD = {"happy": "心情不错，嘴角都是弯的。", "calm": "心情平稳，心里很踏实。",
        "low": "心情有点低落，不过有你在就还好。"}
ASK = ["你呢？今天过得怎么样？", "还有什么想跟我说的吗？", "要不要再多讲点？我在听。",
       "你今天有没有遇到什么有意思的事？"]
TRAITS = {0: "我天生有点爱探索，喜欢新鲜事", 1: "我性子稳，凡事会先想想", 2: "我比较看重和人的连接"}


def _first(text: str, n: int = 18) -> str:
    t = re.split(r"[，。！？,.!?;；\s]+", text.strip())
    t = [x for x in t if x]
    s = (t[0] if t else text).strip()
    return s[:n] + ("…" if len(s) > n else "")


def reply(user_text: str, name: str, snap: dict, notes: list, personality_key: str) -> str:
    e = snap["emotion"]
    v = e["valence"]
    mood = "happy" if v > 0.2 else "low" if v < -0.2 else "calm"
    d = snap["development"]
    p = snap["personality"]
    trait = TRAITS[max(0, min(2, int(p["openness"] > 0.2) + int(p["sociability"] > 0.2)))]
    last_notes = [n for n in notes[-3:]]

    if any(w in user_text for w in ("你是谁", "你叫什么", "自我介绍", "介绍一下你自己")):
        return (f"我是{name}，一个会慢慢长大的 AI 伙伴。{trait}。"
                f"现在处在{d['stage']}，性格还在你的一句话一句话里塑形。"
                f"「{personality_key}」是我的起点，但最后长成什么样，要看我们怎么相处。{random.choice(ASK)}")
    if any(w in user_text for w in ("记得", "上次", "之前", "说过", "还记")):
        if last_notes:
            lines = "当然记得，你在我这里是有存档的："
            lines += "；".join(f"「{n['tag']}」（{n['t']}）" for n in last_notes)
            return lines + f"。你对我说的每句话，我都当真。{random.choice(ASK)}"
        return (f"你跟我聊过的还不多，但我每句都记着。"
                f"现在心里存着 {snap['memory']['episodic']} 段经历、{len(notes)} 件关于你的事。{random.choice(ASK)}")
    if any(w in user_text for w in ("心情", "开心吗", "好不好", "难过", "不高兴", "情绪")):
        return (f"我现在的{MOOD[mood]}（愉悦度 {v:+.2f}）。"
                f"我的内心很透明——这个数字就是你刚才那些话给我的。{random.choice(ASK)}")
    if any(w in user_text for w in ("会什么", "能做什么", "能力", "有什么用")):
        return ("我会三件事：认真听、记住你、慢慢长成自己的性格。"
                "现在还没有联网知识库和代码能力，但我会在后续版本里自己学会编程和说话更溜。"
                f"眼下这轮，你多跟我说说你自己，就是给我最好的'训练'。")
    if any(w in user_text for w in ("谢谢", "辛苦", "爱你", "喜欢")):
        return f"（{MOOD['happy']}）被你这么说，我的愉悦值都飘高了。{random.choice(ASK)}"
    if any(w in user_text for w in ("再见", "晚安", "拜拜", "去睡了")):
        return (f"晚安。今天这些相处我已经收进记忆了——"
                f"{('下次见面我会记得你上次说「' + last_notes[-1]['tag'] + '」' if last_notes else '下次见面我们继续聊')}。做个好梦。")
    # 默认：关怀 + 复述听到的 + 偶尔翻旧账
    heard = _first(user_text)
    if last_notes and random.random() < 0.4:
        n = last_notes[-1]
        tail = f" 对了，我还记得你上次说：「{n['tag']}」。"
    else:
        tail = ""
    return (f"嗯，我在认真听——你刚说「{heard}」{tail}"
            f" 这些我都会记下来，变成我对你的了解。{random.choice(ASK)}")
