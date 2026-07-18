# -*- coding: utf-8 -*-
"""Risk-gated review for Xiaoyou replies.

The critic is deliberately absent from normal casual turns.  It only checks a
draft when local, auditable signals indicate correction, reference resolution,
unsupported factual claims, memory claims or near-duplicate phrasing.
"""

import difflib
import json
import os
import re

from common.log import logger
from plugins.xiaoyou_common.model_gateway import chat_completion
from plugins.xiaoyou_common.thinking_config import build_thinking_payload


CORRECTION_RE = re.compile(
    r"(?:纠正一下|不是这样|不是这个|我改主意|以后不要|现在改成|你理解错|我说的是)"
)
REFERENCE_RE = re.compile(
    r"^(?:(?:它|那个|这个|这件事|那件事)(?:呢|怎么样|怎么办|到了吗|好了吗|还在吗)?|"
    r"后来呢|然后呢|原来的呢|之前那个呢|继续(?:吧|呀|做吧)?)[？?。！!]*$"
)
CONCRETE_CLAIM_RE = re.compile(
    r"(?:你|我)(?:刚才|之前|昨天|昨晚|今天|已经|正在|说过|答应过|买了|到了|发了|穿了|换了|去了|吃了)"
)
MEMORY_CLAIM_RE = re.compile(
    r"(?:我记得|你一直|你从来|你以前|你最喜欢|你答应过|我们说好)"
)
FORBIDDEN_META_RE = re.compile(r"(?:系统提示|提示词|语言模型|内容审核|记忆数据库|作为AI)", re.I)


class SelectiveCritic:
    def enabled(self):
        return os.getenv("XIAOYOU_SELECTIVE_CRITIC_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on"
        )

    def review_if_needed(
        self,
        *,
        current_text,
        draft,
        native_history=None,
        context_plan=None,
        context_pack="",
        recent_state="",
        session_id="",
        trace_id="",
        input_id="",
    ):
        draft = str(draft or "").strip()
        if not self.enabled() or not draft:
            return draft, {"status": "disabled", "risk_reasons": []}

        reasons = self.risk_reasons(
            current_text=current_text,
            draft=draft,
            native_history=native_history,
            context_plan=context_plan,
        )
        if not reasons:
            return draft, {"status": "skipped", "risk_reasons": []}

        prompt = self._build_prompt(
            current_text=current_text,
            draft=draft,
            native_history=native_history,
            context_pack=context_pack,
            recent_state=recent_state,
            risk_reasons=reasons,
        )
        payload = {
            "model": (
                os.getenv("XIAOYOU_SELECTIVE_CRITIC_MODEL")
                or os.getenv("XIAOYOU_CHAT_MODEL")
                or "qwen3.7-plus"
            ),
            "messages": [
                {
                    "role": "system",
                    "content": "你只审查小悠回复中的高风险错误，只输出合法JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.05,
            "max_tokens": 550,
            **build_thinking_payload("XIAOYOU_SELECTIVE_CRITIC"),
        }
        result = chat_completion(
            component="XiaoyouSelectiveCritic",
            purpose="review_high_risk_reply",
            payload=payload,
            timeout=int(os.getenv("XIAOYOU_SELECTIVE_CRITIC_TIMEOUT", "25")),
            session_id=session_id,
            trace_id=trace_id,
            input_id=input_id,
        )
        if not result.ok:
            logger.warning(
                "[SelectiveCritic] review failed; keep draft session=%s error=%s risks=%s",
                session_id,
                getattr(result, "error_kind", "model_failed"),
                ",".join(reasons),
            )
            return draft, {
                "status": "failed_open",
                "risk_reasons": reasons,
                "error": str(getattr(result, "error_kind", "model_failed")),
            }

        data = _parse_json(result.content)
        if not isinstance(data, dict):
            return draft, {"status": "invalid_json", "risk_reasons": reasons}

        action = str(data.get("action") or "accept").strip().lower()
        raw_issues = data.get("issues") if isinstance(data.get("issues"), list) else []
        issues = [
            _clean_line(value, 100)
            for value in raw_issues
            if _clean_line(value, 100)
        ][:5]
        replacement = _clean_reply(data.get("reply"))
        if action != "replace" or not self._replacement_valid(draft, replacement):
            logger.info(
                "[SelectiveCritic] accepted draft session=%s risks=%s issues=%s",
                session_id,
                ",".join(reasons),
                len(issues),
            )
            return draft, {
                "status": "accepted",
                "risk_reasons": reasons,
                "issues": issues,
            }

        logger.info(
            "[SelectiveCritic] minimally replaced draft session=%s risks=%s issues=%s",
            session_id,
            ",".join(reasons),
            len(issues),
        )
        return replacement, {
            "status": "replaced",
            "risk_reasons": reasons,
            "issues": issues,
        }

    def risk_reasons(self, *, current_text, draft, native_history=None, context_plan=None):
        current_text = str(current_text or "").strip()
        draft = str(draft or "").strip()
        plan = context_plan if isinstance(context_plan, dict) else {}
        history = [value for value in (native_history or []) if isinstance(value, dict)]
        reasons = []

        if plan.get("mode") == "correction" or CORRECTION_RE.search(current_text):
            reasons.append("user_correction")
        if history and len(current_text) <= 42 and REFERENCE_RE.search(current_text):
            reasons.append("reference_resolution")
        if CONCRETE_CLAIM_RE.search(draft):
            reasons.append("concrete_claim")
        if plan.get("use_long_memory") and MEMORY_CLAIM_RE.search(draft):
            reasons.append("memory_claim")
        if self._near_duplicate(draft, history):
            reasons.append("near_duplicate")
        return list(dict.fromkeys(reasons))

    def _near_duplicate(self, draft, history):
        probe = _style_normalize(draft)
        if len(probe) < 6:
            return False
        assistant_replies = [
            str(item.get("content") or "").strip()
            for item in history[-12:]
            if item.get("role") == "assistant" and str(item.get("content") or "").strip()
        ][-6:]
        if not assistant_replies:
            return False

        opener = probe[:6]
        opener_matches = 0
        for previous in assistant_replies:
            normalized = _style_normalize(previous)
            if len(normalized) >= 6 and normalized[:6] == opener:
                opener_matches += 1
            if difflib.SequenceMatcher(None, probe, normalized).ratio() >= 0.76:
                return True
        return opener_matches >= 2

    def _replacement_valid(self, draft, replacement):
        if not replacement or replacement == draft:
            return False
        if len(replacement) > 1200 or FORBIDDEN_META_RE.search(replacement):
            return False
        lines = [line for line in replacement.splitlines() if line.strip()]
        return 1 <= len(lines) <= 3

    def _build_prompt(
        self,
        *,
        current_text,
        draft,
        native_history,
        context_pack,
        recent_state,
        risk_reasons,
    ):
        history_lines = []
        for item in (native_history or [])[-10:]:
            if not isinstance(item, dict):
                continue
            role = "YoYo" if item.get("role") == "user" else "小悠"
            content = _clean_line(item.get("content"), 320)
            if content:
                history_lines.append("%s：%s" % (role, content))
        return """下面的回复草稿已经由小悠主模型生成。只有在确实存在高风险错误时才最小幅度替换；不要因为个人措辞偏好、可以更可爱、可以更丰富等理由改写。

允许替换的错误只有：违背YoYo本轮纠正、误解指代或未完话题、编造上下文没有支持的具体事实、把长期记忆说成绝对事实、明显重复近期已经发送的回复。保留小悠原有语气和1到3行微信格式，不增加说教、解释或系统术语。无法确定有错误就accept。

本地风险信号：%s

近期原生角色对话：
%s

当前短时状态：
%s

受预算约束的背景上下文：
%s

YoYo本轮原话：
%s

待检查草稿：
%s

只输出合法JSON：
{"action":"accept|replace","issues":["简短问题"],"reply":"仅replace时填写最小修正版"}""" % (
            ",".join(risk_reasons),
            "\n".join(history_lines) or "暂无",
            str(recent_state or "暂无")[:1800],
            str(context_pack or "暂无")[:5200],
            str(current_text or "")[:1600],
            str(draft or "")[:1200],
        )


def _style_normalize(value):
    text = str(value or "").lower()
    text = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return text


def _clean_line(value, limit):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _clean_reply(value):
    text = str(value or "").strip()
    text = re.sub(r"^```(?:text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip().strip('"“”')[:1200]


def _parse_json(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
