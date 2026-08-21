"""security.conflict_resolver — 矛盾记忆规则消解（standalone 版）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
写入新记忆前，用规则匹配检测是否与已有记忆矛盾：
- 状态翻转（开启↔关闭、启用↔禁用）
- 值变更（端口从A改为B、路径从X改为Y）
- 属性级覆盖（同 category+key 的旧值被新值替代）

消解方式：旧记忆标记 metadata.archived=true（不删除，可回滚）。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bMem0X.conflict_resolver")

# ── 互斥属性规则集 ──
MUTUAL_EXCLUSION_PATTERNS: list[tuple[str, str, str]] = [
    (r"(开关|状态|status|mode)", r"(开启|启用|open|enable|true)", r"(关闭|禁用|close|disable|false)"),
    (r"(开关|状态|status|mode)", r"(关闭|禁用|close|disable|false)", r"(开启|启用|open|enable|true)"),
    (r"(端口|port)", r"\d{4,5}", r"\d{4,5}"),
    (r"(路径|path|目录|dir)", r"[/\\][\w/\\.-]+", r"[/\\][\w/\\.-]+"),
    (r"(版本|version|v\d)", r"v?\d+\.\d+[\.\d]*", r"v?\d+\.\d+[\.\d]*"),
    (r"(从|改为|改成|变成|change)", r"\d+", r"\d+"),
]

_CHANGE_KEYWORDS = re.compile(
    r"(从.{2,30}?改为|从.{2,30}?改成|从.{2,30}?变[为成]|"
    r"端口.{0,5}?改|路径.{0,5}?改|版本.{0,5}?改|状态.{0,5}?改|"
    r"不再|已经不|现在是|改为用|换成了|替换成|"
    r"change(?:d)?\s+to|switch(?:ed)?\s+to|replace(?:d)?\s+with)",
    re.IGNORECASE,
)


def _extract_entity(text: str) -> str:
    """从变更语句中提取变更主体。"""
    m = re.search(r"(端口|路径|目录|版本|版本号|status|mode|状态|配置|地址|URL|端点)\s*(?:从|由|改成|改为|变为)", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r"(端口|路径|目录|版本|状态|配置|地址|URL|端点)\s*[:：]?\s*\S+", text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return ""


def _entity_matches(old_text: str, new_text: str) -> bool:
    """检查新旧记忆是否关于同一实体。"""
    old_entity = _extract_entity(old_text)
    new_entity = _extract_entity(new_text)
    if not old_entity and not new_entity:
        return True
    if old_entity and new_entity:
        return old_entity == new_entity
    return False

# ── SQLite 账本（从 config 读路径） ──
def _get_db_path() -> str:
    from .utils import get_data_dir
    return os.path.join(get_data_dir(), "conflict.db")

_schema_checked = False
_schema_retry_count = 0
_MAX_SCHEMA_RETRIES = 3
_schema_lock = threading.Lock()


def _get_user_id() -> str:
    from .utils import get_user_id
    return get_user_id()

USER_ID = _get_user_id()
AGENT_ID = os.environ.get("MEM0_AGENT_ID", "hermes")


def _get_db() -> sqlite3.Connection:
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema() -> None:
    global _schema_checked, _schema_retry_count
    if _schema_checked:
        return
    if _schema_retry_count >= _MAX_SCHEMA_RETRIES:
        return
    with _schema_lock:
        if _schema_checked:
            return
        conn = _get_db()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conflict_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id   TEXT NOT NULL,
                    old_content TEXT NOT NULL,
                    new_content TEXT NOT NULL,
                    reason      TEXT NOT NULL,
                    rule_type   TEXT NOT NULL,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cf_memory ON conflict_events(memory_id)")
            conn.commit()
            _schema_checked = True
        except Exception as e:
            _schema_retry_count += 1
            logger.warning(f"conflict_events 表初始化失败 (retry {_schema_retry_count}/{_MAX_SCHEMA_RETRIES}): {e}")
        finally:
            conn.close()


def _log_conflict(memory_id: str, old_content: str, new_content: str, reason: str, rule_type: str) -> None:
    _ensure_schema()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO conflict_events (memory_id, old_content, new_content, reason, rule_type) VALUES (?,?,?,?,?)",
            (memory_id, old_content[:500], new_content[:500], reason[:200], rule_type),
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"conflict 事件记录失败: {e}")
    finally:
        conn.close()


def _has_change_signal(text: str) -> bool:
    if not text:
        return False
    return bool(_CHANGE_KEYWORDS.search(text))


def _find_conflicting_patterns(new_text: str) -> list[tuple[str, str, str]]:
    if not _has_change_signal(new_text):
        return []
    return [
        (attr_re, old_re, new_re)
        for attr_re, old_re, new_re in MUTUAL_EXCLUSION_PATTERNS
        if re.search(new_re, new_text, re.IGNORECASE)
    ]


def _text_matches_old_pattern(text: str, old_re: str) -> bool:
    return bool(re.search(old_re, text, re.IGNORECASE))


def detect_and_resolve(memory, new_text: str, filters: dict = None) -> Optional[dict]:
    """写入前矛盾检测入口。返回 None → 无矛盾。"""
    if not new_text or len(new_text) < 15:
        return None

    triggered = _find_conflicting_patterns(new_text)
    if not triggered:
        return None

    if filters is None:
        filters = {"user_id": USER_ID, "agent_id": AGENT_ID}

    try:
        try:
            raw = memory.search(new_text, filters=filters, top_k=20)
        except TypeError:
            raw = memory.search(new_text, filters=filters, limit=20)
        results = raw.get("results", raw) if isinstance(raw, dict) else raw
        if not isinstance(results, list):
            return None
    except Exception as e:
        logger.debug(f"矛盾检测搜索失败: {e}")
        return None

    conflicts = []
    for r in results:
        if not isinstance(r, dict):
            continue
        mid = r.get("id", "")
        old_text = (r.get("memory") or "").strip()
        if not mid or not old_text:
            continue
        meta = r.get("metadata") or {}

        # 跳过已归档和已删除的记忆
        if meta.get("archived") or meta.get("deleted_at"):
            continue

        # 只考虑 P0/P1 优先级的记忆
        if not re.search(r"\[P[01]\]", old_text[:50]):
            continue

        # 实体对齐：新旧记忆必须关于同一实体
        if not _entity_matches(old_text, new_text):
            continue

        for attr_re, old_re, new_re in triggered:
            old_attr_match = re.search(attr_re, old_text[:500], re.IGNORECASE)
            new_attr_match = re.search(attr_re, new_text[:500], re.IGNORECASE)
            if not old_attr_match or not new_attr_match:
                continue
            if old_attr_match.group(0).lower() != new_attr_match.group(0).lower():
                continue
            if _text_matches_old_pattern(old_text[:500], old_re) and _text_matches_old_pattern(new_text[:500], new_re):
                old_val = re.search(old_re, old_text[:500], re.IGNORECASE)
                new_val = re.search(new_re, new_text[:500], re.IGNORECASE)
                if old_val and new_val and old_val.group(0) == new_val.group(0):
                    continue
                old_meta = dict(meta)
                old_meta["archived"] = True
                old_meta["archived_by"] = "conflict_resolver"
                old_meta["superseded_by"] = new_text[:200]
                try:
                    memory.update(mid, old_text, metadata=old_meta)
                    _log_conflict(mid, old_text, new_text, f"规则: {old_re}→{new_re}", "pattern_match")
                    conflicts.append({
                        "memory_id": mid,
                        "old_content": old_text[:100],
                        "reason": f"规则触发: {old_re} → {new_re}",
                    })
                    logger.info("⚔️ conflict: id=%s 归档（旧='%s'）", mid[:8], old_text[:60])
                except Exception as e:
                    logger.debug(f"归档失败 {mid[:8]}: {e}")
                break

    if not conflicts:
        return None

    return {
        "resolved": len(conflicts),
        "conflicts": conflicts,
        "action": "archive_old",
    }


def rollback_conflict(memory_id: str, memory=None) -> dict:
    """回滚一次矛盾消解。"""
    if memory is None:
        return {"status": "error", "detail": "mem0 实例未传入"}

    try:
        got = memory.get(memory_id)
        if not isinstance(got, dict):
            return {"status": "error", "detail": f"记忆 {memory_id[:8]} 不存在"}

        old_text = got.get("memory") or got.get("content") or ""
        old_meta = got.get("metadata") or {}
        old_meta.pop("archived", None)
        old_meta.pop("archived_by", None)
        old_meta.pop("superseded_by", None)
        memory.update(memory_id, old_text, metadata=old_meta)

        logger.info("↩️ conflict 回滚: %s", memory_id[:8])
        return {"status": "ok", "memory_id": memory_id}

    except Exception as e:
        return {"status": "error", "detail": str(e)}


def list_conflicts(limit: int = 20) -> list[dict]:
    """列出矛盾消解记录。"""
    _ensure_schema()
    conn = _get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM conflict_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
        return rows
    finally:
        conn.close()
