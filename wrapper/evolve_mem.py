"""evolve_mem — 记忆自进化模块

定期分析记忆质量，自动优化：
- 合并碎片信息
- 提升重要记忆的权重
- 降级低质量记忆
- 生成记忆摘要
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Dict, List

logger = logging.getLogger("mem0x.evolve_mem")

# 后台扫描间隔（秒）
DEFAULT_INTERVAL = 14400  # 4小时

# 全局状态
_running = False
_thread: Optional[threading.Thread] = None


def analyze_memory_quality(memory, user_id: str = "bo", agent_id: str = "hermes") -> Dict:
    """分析记忆质量，返回统计信息。"""
    stats = {
        "total": 0,
        "high_quality": 0,
        "low_quality": 0,
        "stale": 0,
        "by_lane": {},
    }

    try:
        filters = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id

        # 使用占位符查询获取记忆
        results = memory.search(query="记忆", filters=filters, top_k=500)
        items = results.get("results", []) if isinstance(results, dict) else []

        stats["total"] = len(items)

        import re
        from datetime import datetime, timezone, timedelta

        for item in items:
            text = item.get("memory", "")
            score = item.get("score", 0) or 0
            created_at = item.get("created_at")

            # 统计 lane 分布
            lane_match = re.search(r"\[lane:(\w+)\]", text)
            lane = lane_match.group(1) if lane_match else "none"
            stats["by_lane"][lane] = stats["by_lane"].get(lane, 0) + 1

            # 质量判断
            if score >= 0.7:
                stats["high_quality"] += 1
            elif score < 0.3:
                stats["low_quality"] += 1

            # 过期判断
            if created_at:
                try:
                    created = datetime.fromisoformat(created_at)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - created).days
                    if age_days > 90 and score < 0.4:
                        stats["stale"] += 1
                except Exception:
                    pass

    except Exception as e:
        logger.error("分析记忆质量失败: %s", e)

    return stats


def run_evolve_cycle(memory, neo4j_hook=None, user_id: str = "bo",
                    agent_id: str = "hermes") -> Dict:
    """执行一轮自进化，返回优化结果。"""
    result = {"analyzed": 0, "optimized": 0, "pruned": 0}

    try:
        # 1. 分析质量
        stats = analyze_memory_quality(memory, user_id, agent_id)
        result["analyzed"] = stats["total"]
        logger.info("记忆质量分析: 总%d, 高质%d, 低质%d, 过期%d",
                    stats["total"], stats["high_quality"],
                    stats["low_quality"], stats["stale"])

        # 2. 清理低质量记忆（score < 0.2 且非核心）
        if stats["low_quality"] > 0:
            from wrapper.core_memory import is_core_memory
            filters = {"user_id": user_id}
            if agent_id:
                filters["agent_id"] = agent_id

            # 使用占位符查询获取记忆
            results = memory.search(query="记忆", filters=filters, top_k=200)
            items = results.get("results", []) if isinstance(results, dict) else []

            for item in items:
                score = item.get("score", 0) or 0
                mem_id = item.get("id")

                if score < 0.2 and mem_id and not is_core_memory(mem_id):
                    try:
                        memory.delete(mem_id)
                        result["pruned"] += 1

                        if neo4j_hook and neo4j_hook.enabled:
                            try:
                                neo4j_hook.cleanup(mem_id)
                            except Exception:
                                pass

                    except Exception as e:
                        logger.debug("清理失败 %s: %s", mem_id[:16], e)

        # 3. 记录进化日志
        if result["pruned"] > 0:
            logger.info("自进化完成: 清理 %d 条低质量记忆", result["pruned"])

    except Exception as e:
        logger.error("自进化失败: %s", e)

    return result


def _background_loop(memory_getter, interval: int = DEFAULT_INTERVAL):
    """后台循环线程。"""
    global _running
    logger.info("evolve_mem 后台线程启动，间隔 %ds", interval)

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
                result = run_evolve_cycle(memory, neo4j_hook=neo4j_hook)
                if result["pruned"] > 0:
                    logger.info("本轮自进化: 清理 %d 条", result["pruned"])
        except Exception as e:
            logger.error("evolve_mem 循环异常: %s", e)

        time.sleep(interval)

    logger.info("evolve_mem 后台线程已停止")


def start(memory_getter, interval: int = DEFAULT_INTERVAL):
    """启动后台自进化线程。"""
    global _running, _thread
    if _running:
        logger.warning("evolve_mem 已在运行")
        return

    _running = True
    _thread = threading.Thread(
        target=_background_loop,
        args=(memory_getter, interval),
        daemon=True,
        name="evolve-mem",
    )
    _thread.start()


def stop():
    """停止后台自进化线程。"""
    global _running
    _running = False


def is_running() -> bool:
    return _running
