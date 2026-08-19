"""security.pipeline — 写入链路编排（standalone 版）

完整链路：注入防御→PII脱敏→搜索候选→Jaccard去重→矛盾消解→LLM语义判重→存入
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .injection_guard import validate_memory_content
from .dedup import find_duplicate
from .conflict_resolver import detect_and_resolve
from .self_edit import self_edit_on_add

logger = logging.getLogger("bMem0X.pipeline")

# ── 脱敏配置 ──
_REDACT_MAP: dict[str, str] = {
    "何博洋": "admin",
}
_REDACT_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_REDACT_MAP, key=len, reverse=True))
) if _REDACT_MAP else None


def add_redact_name(name: str, replacement: str) -> None:
    """动态添加脱敏映射。"""
    _REDACT_MAP[name] = replacement
    global _REDACT_RE
    _REDACT_RE = re.compile(
        "|".join(re.escape(k) for k in sorted(_REDACT_MAP, key=len, reverse=True))
    )


def redact_pii(text: str) -> str:
    """脱敏处理：将已知真实姓名替换为脱敏名称。"""
    if not _REDACT_RE or not text:
        return text
    result = _REDACT_RE.sub(lambda m: _REDACT_MAP[m.group()], text)
    if result != text:
        logger.info("PII redacted")
    return result


def safe_add(
    memory,
    content: str,
    filters: dict = None,
    *,
    user_id: str = None,
    agent_id: str = None,
    metadata: dict = None,
    expiration_date: str = None,
    infer: bool = False,
) -> dict:
    """安全写入链路：注入防御→脱敏→去重→矛盾消解→语义判重→存入。

    Args:
        memory: mem0 Memory 实例
        content: 要写入的文本
        filters: mem0 search filters
        user_id/agent_id: 写入身份
        metadata: 附加 metadata
        expiration_date: 过期日期 YYYY-MM-DD
        infer: 是否用 LLM 提取事实（默认 False，因为 pipeline 自己做判重）

    Returns:
        {"action": "added"|"duplicate"|"conflict"|"semantic"|"rejected", ...}
    """
    # 1. 注入防御
    is_valid, content, reject_reason = validate_memory_content(content)
    if not is_valid or not content:
        return {"action": "rejected", "reason": reject_reason or "empty content"}

    # 1.5 PII 脱敏（必须在搜索之前，确保搜索用脱敏文本）
    content = redact_pii(content)

    # 2. 搜索一次（共享给 dedup + self_edit，省一次 embedder 调用）
    if filters is None:
        filters = {}
        if user_id:
            filters["user_id"] = user_id
        if agent_id:
            filters["agent_id"] = agent_id
    # mem0 2.0+ 要求 filters 至少有一个 entity ID
    if not filters.get("user_id") and not filters.get("agent_id") and not filters.get("run_id"):
        filters["user_id"] = "bo"  # 默认用户

    shared_results = []
    try:
        raw = memory.search(content, filters=filters, top_k=5)
        shared_results = raw.get("results", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    except Exception:
        pass

    # 3. Jaccard 去重（用共享结果）
    dup = find_duplicate(memory, content, filters, _pre_results=shared_results)
    if dup:
        mem_id, old_text, sim = dup
        try:
            memory.update(mem_id, content)
        except Exception as e:
            logger.warning("dedup update 失败: %s", e)
            return {"action": "error", "reason": f"dedup update failed: {e}"}
        return {"action": "duplicate", "memory_id": mem_id, "similarity": sim}

    # 4. 矛盾消解（规则驱动，零 LLM 成本）
    conflict_result = detect_and_resolve(memory, content, filters=filters)
    if conflict_result:
        # 矛盾消解后，仍需经过 LLM 语义判重
        edit_result = self_edit_on_add(memory, content, _pre_candidates=shared_results)
        if edit_result:
            return {
                "action": "semantic", "memory_id": edit_result["memory_id"],
                "sub_action": edit_result["action"], "confidence": edit_result["confidence"],
                "reason": edit_result["reason"],
            }
        # 语义判重通过，写入新记忆
        try:
            add_kwargs = {
                "user_id": user_id,
                "agent_id": agent_id,
                "infer": infer,
                "metadata": metadata,
            }
            if expiration_date is not None:
                add_kwargs["expiration_date"] = expiration_date
            result = memory.add(
                [{"role": "user", "content": content}],
                **add_kwargs,
            )
            results = result.get("results", []) if isinstance(result, dict) else []
            memory_id = results[0].get("id") if results else None
        except Exception as e:
            logger.warning("safe_add conflict后写入异常: %s", e)
            memory_id = None
        return {
            "action": "conflict", "resolved": conflict_result["resolved"],
            "conflicts": conflict_result["conflicts"], "memory_id": memory_id,
        }

    # 5. LLM 语义判重（用共享候选）
    edit_result = self_edit_on_add(memory, content, _pre_candidates=shared_results)
    if edit_result:
        return {
            "action": "semantic", "memory_id": edit_result["memory_id"],
            "sub_action": edit_result["action"], "confidence": edit_result["confidence"],
            "reason": edit_result["reason"],
        }

    # 6. 正常写入
    try:
        add_kwargs = {
            "user_id": user_id,
            "agent_id": agent_id,
            "infer": infer,
            "metadata": metadata,
        }
        if expiration_date is not None:
            add_kwargs["expiration_date"] = expiration_date
        result = memory.add(
            [{"role": "user", "content": content}],
            **add_kwargs,
        )
        results = result.get("results", []) if isinstance(result, dict) else []
        memory_id = results[0].get("id") if results else None
        return {"action": "added", "memory_id": memory_id}
    except Exception as e:
        logger.warning("safe_add 异常: %s", e)
        return {"action": "error", "reason": str(e)}
