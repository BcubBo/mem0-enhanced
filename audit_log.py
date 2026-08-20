# ================================================================
# mem0x · audit_log.py · 审计日志模块
# ▍这是什么
# ① 干什么：记录每次 /add, /search, /delete, /update 操作的审计日志
# ② 技术栈：SQLite（Python标准库，无额外依赖）
# ③ 依赖：无
# ④ 给谁看：需要查询历史操作、排查问题、记忆溯源的人
# ▍修改铁律
# 1. 所有写操作必须在 try/except 中，日志失败不影响主业务
# 2. content_summary 只存前 100 字符，不存完整内容
# 3. 按日期自动清理旧日志，默认保留 30 天
# ================================================================

"""审计日志模块 — 记录 mem0x 的所有操作。"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mem0x.audit")

# 默认配置
DEFAULT_RETENTION_DAYS = 30
DEFAULT_SUMMARY_MAX_LEN = 100


class AuditLogger:
    """审计日志记录器。"""

    def __init__(
        self,
        db_path: str = "audit_log.db",
        retention_days: int = DEFAULT_RETENTION_DAYS,
        summary_max_len: int = DEFAULT_SUMMARY_MAX_LEN,
    ):
        self._db_path = db_path
        self._retention_days = retention_days
        self._summary_max_len = summary_max_len
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """初始化数据库，创建表。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        sender_open_id TEXT,
                        user_name TEXT,
                        chat_id TEXT,
                        chat_type TEXT,
                        platform TEXT,
                        content_summary TEXT,
                        result TEXT,
                        memory_id TEXT,
                        metadata_json TEXT
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_log(timestamp)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_operation ON audit_log(operation)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sender ON audit_log(sender_open_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat ON audit_log(chat_id)
                """)
                conn.commit()
                logger.info("审计日志数据库初始化完成: %s", self._db_path)
        except Exception as e:
            logger.error("审计日志数据库初始化失败: %s", e)

    def _make_summary(self, content: str) -> str:
        """生成内容摘要。"""
        if not content:
            return ""
        content = content.strip()
        if len(content) <= self._summary_max_len:
            return content
        return content[:self._summary_max_len] + "..."

    def log(
        self,
        operation: str,
        *,
        sender_open_id: str = "",
        user_name: str = "",
        chat_id: str = "",
        chat_type: str = "",
        platform: str = "",
        content: str = "",
        result: str = "",
        memory_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """记录一条审计日志。"""
        try:
            timestamp = datetime.utcnow().isoformat() + "Z"
            content_summary = self._make_summary(content)
            import json
            metadata_json = json.dumps(metadata) if metadata else None

            with self._lock:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        """INSERT INTO audit_log 
                        (timestamp, operation, sender_open_id, user_name, 
                         chat_id, chat_type, platform, content_summary, 
                         result, memory_id, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (timestamp, operation, sender_open_id, user_name,
                         chat_id, chat_type, platform, content_summary,
                         result, memory_id, metadata_json),
                    )
                    conn.commit()
        except Exception as e:
            logger.warning("审计日志写入失败: %s", e)

    def log_add(
        self,
        *,
        sender_open_id: str = "",
        user_name: str = "",
        chat_id: str = "",
        chat_type: str = "",
        platform: str = "",
        content: str = "",
        result: str = "",
        memory_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """记录 add 操作。"""
        self.log(
            "add",
            sender_open_id=sender_open_id,
            user_name=user_name,
            chat_id=chat_id,
            chat_type=chat_type,
            platform=platform,
            content=content,
            result=result,
            memory_id=memory_id,
            metadata=metadata,
        )

    def log_search(
        self,
        *,
        sender_open_id: str = "",
        user_name: str = "",
        chat_id: str = "",
        chat_type: str = "",
        platform: str = "",
        query: str = "",
        result_count: int = 0,
    ):
        """记录 search 操作。"""
        self.log(
            "search",
            sender_open_id=sender_open_id,
            user_name=user_name,
            chat_id=chat_id,
            chat_type=chat_type,
            platform=platform,
            content=query,
            result=f"found {result_count} results",
        )

    def log_delete(
        self,
        *,
        memory_id: str = "",
        confirm: bool = False,
        result: str = "",
    ):
        """记录 delete 操作。"""
        self.log(
            "delete",
            memory_id=memory_id,
            result=result,
            metadata={"confirm": confirm},
        )

    def log_update(
        self,
        *,
        memory_id: str = "",
        content: str = "",
        result: str = "",
    ):
        """记录 update 操作。"""
        self.log(
            "update",
            memory_id=memory_id,
            content=content,
            result=result,
        )

    def query(
        self,
        *,
        operation: Optional[str] = None,
        sender_open_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """查询审计日志。"""
        try:
            conditions = []
            params = []

            if operation:
                conditions.append("operation = ?")
                params.append(operation)
            if sender_open_id:
                conditions.append("sender_open_id = ?")
                params.append(sender_open_id)
            if chat_id:
                conditions.append("chat_id = ?")
                params.append(chat_id)
            if start_time:
                conditions.append("timestamp >= ?")
                params.append(start_time)
            if end_time:
                conditions.append("timestamp <= ?")
                params.append(end_time)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            query = f"""
                SELECT id, timestamp, operation, sender_open_id, user_name,
                       chat_id, chat_type, platform, content_summary, result,
                       memory_id, metadata_json
                FROM audit_log
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            results = []
            for row in rows:
                import json
                result = {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "operation": row["operation"],
                    "sender_open_id": row["sender_open_id"],
                    "user_name": row["user_name"],
                    "chat_id": row["chat_id"],
                    "chat_type": row["chat_type"],
                    "platform": row["platform"],
                    "content_summary": row["content_summary"],
                    "result": row["result"],
                    "memory_id": row["memory_id"],
                }
                if row["metadata_json"]:
                    try:
                        result["metadata"] = json.loads(row["metadata_json"])
                    except Exception:
                        pass
                results.append(result)

            return results
        except Exception as e:
            logger.warning("审计日志查询失败: %s", e)
            return []

    def cleanup_old_logs(self):
        """清理超过保留期的旧日志。"""
        try:
            cutoff = datetime.utcnow() - timedelta(days=self._retention_days)
            cutoff_str = cutoff.isoformat() + "Z"

            with self._lock:
                with sqlite3.connect(self._db_path) as conn:
                    cursor = conn.execute(
                        "DELETE FROM audit_log WHERE timestamp < ?",
                        (cutoff_str,),
                    )
                    deleted = cursor.rowcount
                    conn.commit()

            if deleted > 0:
                logger.info("清理了 %d 条旧审计日志", deleted)
        except Exception as e:
            logger.warning("清理旧审计日志失败: %s", e)

    def get_stats(self) -> Dict[str, Any]:
        """获取审计日志统计信息。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                # 总记录数
                cursor = conn.execute("SELECT COUNT(*) FROM audit_log")
                total = cursor.fetchone()[0]

                # 按操作类型统计
                cursor = conn.execute(
                    "SELECT operation, COUNT(*) FROM audit_log GROUP BY operation"
                )
                by_operation = {row[0]: row[1] for row in cursor.fetchall()}

                # 最近一条记录
                cursor = conn.execute(
                    "SELECT timestamp FROM audit_log ORDER BY timestamp DESC LIMIT 1"
                )
                row = cursor.fetchone()
                last_record = row[0] if row else None

                # 数据库大小
                db_size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0

                return {
                    "total_records": total,
                    "by_operation": by_operation,
                    "last_record": last_record,
                    "db_size_bytes": db_size,
                    "retention_days": self._retention_days,
                }
        except Exception as e:
            logger.warning("获取审计日志统计失败: %s", e)
            return {"error": str(e)}


# 全局实例
_audit_logger: Optional[AuditLogger] = None
_audit_lock = threading.Lock()


def get_audit_logger() -> Optional[AuditLogger]:
    """获取审计日志记录器实例。"""
    global _audit_logger
    if _audit_logger is None:
        with _audit_lock:
            if _audit_logger is None:
                try:
                    # 从环境变量获取数据库路径，默认在当前目录
                    db_path = os.environ.get("MEM0X_AUDIT_DB", "audit_log.db")
                    retention_days = int(os.environ.get("MEM0X_AUDIT_RETENTION_DAYS", "30"))
                    _audit_logger = AuditLogger(
                        db_path=db_path,
                        retention_days=retention_days,
                    )
                except Exception as e:
                    logger.error("创建审计日志记录器失败: %s", e)
                    return None
    return _audit_logger
