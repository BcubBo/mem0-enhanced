"""auto_expire — 自动过期清理模块

后台线程定时扫描过期记忆并删除。
参考 aizuMEI ducky/background/auto_expire.py
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("mem0x.auto_expire")

# 默认扫描间隔（秒）
DEFAULT_INTERVAL = 3600  # 1小时

# 全局状态
_running = False
_thread: Optional[threading.Thread] = None


def _parse_lane_ttl(lane: str) -> Optional[int]:
    """根据 lane 标签返回 TTL 天数。"""
    lane_ttl = {
        "identity": None,      # 永不衰减
        "preference": None,    # 永不衰减
        "project": 180,        # 180天
        "emotion": 5,          # 5天
        "default": 30,         # 30天
    }
    return lane_ttl.get(lane)


def _extract_lane(memory_text: str) -> Optional[str]:
    """从记忆文本中提取 lane 标签。"""
    import re
    match = re.search(r"\[lane:(\w+)\]", memory_text)
    return match.group(1) if match else None


def _extract_expiration(memory_text: str) -> Optional[str]:
    """从记忆文本中提取过期日期。"""
    import re
    match = re.search(r"\[expires:(\d{4}-\d{2}-\d{2})\]", memory_text)
    return match.group(1) if match else None


def _is_expired(memory_text: str, created_at: Optional[str] = None) -> bool:
    """判断记忆是否过期。"""
    # 1. 检查显式 expires 标记
    exp_str = _extract_expiration(memory_text)
    if exp_str:
        try:
            exp_date = datetime.fromisoformat(exp_str)
            if exp_date.tzinfo is None:
                exp_date = exp_date.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > exp_date
        except ValueError:
            pass

    # 2. 检查 lane TTL
    lane = _extract_lane(memory_text)
    if lane and created_at:
        ttl_days = _parse_lane_ttl(lane)
        if ttl_days is None:
            return False  # 永不衰减
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            from datetime import timedelta
            expire_at = created + timedelta(days=ttl_days)
            return datetime.now(timezone.utc) > expire_at
        except ValueError:
            pass

    return False


def run_expire_cycle(memory, neo4j_hook=None) -> int:
    """执行一轮过期清理，返回删除数量。

    Args:
        memory: mem0 实例
        neo4j_hook: Neo4j hook 实例（可选），用于同步清理图谱
    """
    deleted = 0
    try:
        # 搜索所有记忆（分页扫描）
        offset = 0
        batch_size = 100
        while True:
            # 使用占位符查询获取记忆
            results = memory.search(
                query="记忆",
                filters={},
                top_k=batch_size,
                offset=offset,
            )
            items = results.get("results", []) if isinstance(results, dict) else []
            if not items:
                break

            for item in items:
                mem_id = item.get("id")
                mem_text = item.get("memory", "")
                created_at = item.get("created_at")

                if mem_id and _is_expired(mem_text, created_at):
                    # 跳过核心记忆
                    from wrapper.core_memory import is_core_memory
                    if is_core_memory(mem_id):
                        continue

                    try:
                        # 1. 删除 Qdrant 记忆
                        memory.delete(mem_id)
                        deleted += 1
                        logger.info("已删除过期记忆: %s", mem_id[:16])

                        # 2. 同步清理 Neo4j
                        if neo4j_hook and neo4j_hook.enabled:
                            try:
                                neo4j_hook.cleanup(mem_id)
                            except Exception as e:
                                logger.debug("Neo4j cleanup 失败 %s: %s", mem_id[:16], e)

                    except Exception as e:
                        logger.warning("删除失败 %s: %s", mem_id[:16], e)

            offset += batch_size
            if len(items) < batch_size:
                break

    except Exception as e:
        logger.error("过期清理失败: %s", e)

    return deleted


def _background_loop(memory_getter, interval: int = DEFAULT_INTERVAL):
    """后台循环线程。"""
    global _running
    logger.info("auto_expire 后台线程启动，间隔 %ds", interval)

    # 延迟获取 Neo4j hook（可能未启用）
    from wrapper.neo4j_hook import get_hook
    neo4j_hook = None
    try:
        neo4j_hook = get_hook()
    except Exception:
        pass

    while _running:
        try:
            memory = memory_getter()
            if memory:
                deleted = run_expire_cycle(memory, neo4j_hook=neo4j_hook)
                if deleted > 0:
                    logger.info("本轮清理 %d 条过期记忆", deleted)
        except Exception as e:
            logger.error("auto_expire 循环异常: %s", e)

        time.sleep(interval)

    logger.info("auto_expire 后台线程已停止")


def start(memory_getter, interval: int = DEFAULT_INTERVAL):
    """启动后台清理线程。"""
    global _running, _thread
    if _running:
        logger.warning("auto_expire 已在运行")
        return

    _running = True
    _thread = threading.Thread(
        target=_background_loop,
        args=(memory_getter, interval),
        daemon=True,
        name="auto-expire",
    )
    _thread.start()


def stop():
    """停止后台清理线程。"""
    global _running
    _running = False


def is_running() -> bool:
    return _running
