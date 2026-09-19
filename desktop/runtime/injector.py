"""上下文注入器 —— 在模型说话之前，先把该知道的东西准备好。

真实问题
--------
智能体"看起来不聪明"，很多时候不是模型不行，而是**说话时手上没有材料**：
没检索到相关记忆、不知道当前情绪、不知道现在在聊什么话题。
等到模型已经生成完回复再去查，就晚了。

做法（ACI：Agent Context Injection）
----------------------------------
在用户输入进来、但还没交给模型之前，同步做几件小事：

1. 检索长期记忆（装了认知层就走语义，否则退回字面）
2. 取当前情绪值
3. 取当前焦点（"现在在聊什么"）
4. 组装成一小段**可直接拼进系统提示词**的文本

这一步是纯本地、毫秒级、不联网，所以放在同步路径上是安全的。

产出给谁用
----------
:meth:`build` 返回的 :class:`InjectedContext` 既有给人看的 ``text``，
也有给程序用的 ``sources``（每条记忆的来源与分数），
便于调试"为什么模型会这么说"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["InjectedContext", "ContextInjector"]


@dataclass
class InjectedContext:
    """一次注入的产物。"""

    query: str
    text: str = ""
    memories: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    mood: float = 0.0
    focus: str = ""
    #: 实际使用的检索模式（semantic / lexical / none），排障用
    mode: str = "none"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query, "text": self.text,
            "memories": self.memories, "sources": self.sources,
            "mood": self.mood, "focus": self.focus, "mode": self.mode,
        }

    def __bool__(self) -> bool:
        return bool(self.text)


class ContextInjector:
    """按输入预取认知上下文。

    参数
    ----
    agent : 任何有 ``recall`` / ``mood`` 的对象（``BaseAgent`` 即可）
    cognition : 可选的认知中枢（``pasm_skills.cognition.CognitionHub``）；
        给了就走语义检索，没给就用 ``agent.recall``
    max_memories : 最多注入几条记忆
    """

    def __init__(self, agent: Any = None, cognition: Any = None,
                 max_memories: int = 5, include_mood: bool = True,
                 include_focus: bool = True):
        self.agent = agent
        self.cognition = cognition
        self.max_memories = max(1, int(max_memories))
        self.include_mood = bool(include_mood)
        self.include_focus = bool(include_focus)

    # ------- 检索 -------------------------------------------------

    def _recall(self, query: str, k: int) -> List[Dict[str, Any]]:
        if self.cognition is not None:
            try:
                return list(self.cognition.recall(query, k=k))
            except Exception:
                pass
        if self.agent is not None:
            try:
                return list(self.agent.recall(query, k=k))
            except Exception:
                return []
        return []

    @staticmethod
    def _mode_of(cognition: Any) -> str:
        if cognition is None:
            return "lexical"
        try:
            return "semantic"
        except Exception:
            return "lexical"

    # ------- 组装 -------------------------------------------------

    def build(self, query: str, extra: Optional[Dict[str, Any]] = None) -> InjectedContext:
        """按输入组装上下文。**不写记忆**（检索可能产生复习副作用，这是有意的）。"""
        extra = dict(extra or {})
        ctx = InjectedContext(query=query, mode=self._mode_of(self.cognition))

        mems = self._recall(query, self.max_memories)
        # 去重：同一件事被反复写入会产生多条内容相同的记忆
        seen = set()
        for m in mems:
            sig = (str(m.get("title", "")), str(m.get("brief", "")))
            if sig in seen:
                continue
            seen.add(sig)
            ctx.memories.append({
                "title": m.get("title", ""),
                "brief": m.get("brief", ""),
                "tags": list(m.get("tags") or []),
                "salience": m.get("sal", m.get("salience", 1)),
            })
            ctx.sources.append({
                "title": m.get("title", ""),
                "score": m.get("score"),
                "semantic": m.get("semantic"),
                "retention": m.get("retention"),
            })

        # 情绪
        if self.include_mood:
            if "mood" in extra:
                ctx.mood = float(extra["mood"])
            elif self.agent is not None:
                try:
                    ctx.mood = float(getattr(self.agent, "mood", 0.0))
                except Exception:
                    ctx.mood = 0.0

        # 焦点
        if self.include_focus and self.cognition is not None:
            try:
                ctx.focus = self.cognition.focus.to_prompt()
            except Exception:
                ctx.focus = ""

        ctx.text = self.render(ctx)
        return ctx

    def render(self, ctx: InjectedContext) -> str:
        """把上下文渲染成提示词片段。"""
        parts: List[str] = []
        if ctx.memories:
            lines = []
            for m in ctx.memories:
                brief = m.get("brief") or ""
                line = "· %s" % m.get("title", "")
                if brief:
                    line += "：%s" % brief
                lines.append(line)
            parts.append("你记得这些事：\n" + "\n".join(lines))
        if ctx.focus:
            parts.append(ctx.focus)
        if ctx.mood > 0.25:
            parts.append("你现在心情不错。")
        elif ctx.mood < -0.25:
            parts.append("你现在有点低落。")
        return "\n".join(parts).strip()

    def __repr__(self) -> str:  # pragma: no cover
        return "<ContextInjector mode=%s max_memories=%d>" % (
            self._mode_of(self.cognition), self.max_memories)
