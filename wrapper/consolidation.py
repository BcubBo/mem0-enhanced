"""consolidation — 记忆整合模块

合并相似碎片记忆，减少冗余。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Dict, Optional

logger = logging.getLogger("mem0x.consolidation")

# 相似度阈值：高于此值的记忆视为可合并
SIMILARITY_THRESHOLD = 0.85

# 后台扫描间隔（秒）
DEFAULT_INTERVAL = 7200  # 2小时

# 全局状态
_running = False
_thread: Optional[threading.Thread] = None


def _compute_similarity(text_a: str, text_b: str) -> float:
    """计算两段文本的相似度（简单 Jaccard）。"""
    set_a = set(text_a.split())
    set_b = set(text_b.split())
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _merge_memories(memory_a: Dict, memory_b: Dict) -> str:
    """合并两条记忆，保留更完整的信息。"""
    text_a = memory_a.get("memory", "")
    text_b = memory_b.get("memory", "")
    # 选择更长的作为基础（通常信息更完整）
    if len(text_a) >= len(text_b):
        return text_a
    return text_b


def find_duplicate_pairs(memory, user_id: str = "bo", agent_id: str = "hermes",
                         limit: int = 50, threshold: float = SIMILARITY_THRESHOLD) -> List[tuple]:
    """查找可合并的重复记忆对。

    Returns:
        list of (id_a, id_b, similarity) 元组
    """
    pairs = []
    try:
        # 获取用户的所有记忆
        filters = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id

        # 使用占位符查询获取记忆
        results = memory.search(
            query="记忆",
            filters=filters,
            top_k=limit,
        )
        items = results.get("results", []) if isinstance(results, dict) else []

        # 两两比较
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                text_a = items[i].get("memory", "")
                text_b = items[j].get("memory", "")
                sim = _compute_similarity(text_a, text_b)
                if sim >= threshold:
                    pairs.append((
                        items[i].get("id"),
                        items[j].get("id"),
                        sim,
                    ))

    except Exception as e:
        logger.error("查找重复记忆失败: %s", e)

    return pairs


def run_consolidation_cycle(memory, neo4j_hook=None, user_id: str = "bo",
                           agent_id: str = "hermes") -> int:
    """执行一轮记忆整合，返回合并数量。

    策略：找到相似度 >= threshold 的记忆对，删除较差的那条。
    """
    merged = 0
    try:
        pairs = find_duplicate_pairs(memory, user_id, agent_id)
        logger.info("发现 %d 对可合并记忆", len(pairs))

        # 按相似度降序处理
        pairs.sort(key=lambda x: x[2], reverse=True)

        deleted_ids = set()  # 避免重复删除
        for id_a, id_b, sim in pairs:
            if id_a in deleted_ids or id_b in deleted_ids:
                continue

            # 获取两条记忆
            # search 返回的结果已经包含内容，但我们用 id 去重
            # 这里简单删除 id_b（后插入的）
            try:
                memory.delete(id_b)
                deleted_ids.add(id_b)
                merged += 1
                logger.info("合并记忆: %s <- %s (相似度 %.2f)", id_a[:16], id_b[:16], sim)

                # 同步清理 Neo4j
                if neo4j_hook and neo4j_hook.enabled:
                    try:
                        neo4j_hook.cleanup(id_b)
                    except Exception as e:
                        logger.debug("Neo4j cleanup 失败 %s: %s", id_b[:16], e)

            except Exception as e:
                logger.warning("合并失败 %s <- %s: %s", id_a[:16], id_b[:16], e)

    except Exception as e:
        logger.error("记忆整合失败: %s", e)

    return merged


def _background_loop(memory_getter, interval: int = DEFAULT_INTERVAL):
    """后台循环线程。"""
    global _running
    logger.info("consolidation 后台线程启动，间隔 %ds", interval)

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
                merged = run_consolidation_cycle(memory, neo4j_hook=neo4j_hook)
                if merged > 0:
                    logger.info("本轮整合 %d 条记忆", merged)
        except Exception as e:
            logger.error("consolidation 循环异常: %s", e)

        time.sleep(interval)

    logger.info("consolidation 后台线程已停止")


def start(memory_getter, interval: int = DEFAULT_INTERVAL):
    """启动后台整合线程。"""
    global _running, _thread
    if _running:
        logger.warning("consolidation 已在运行")
        return

    _running = True
    _thread = threading.Thread(
        target=_background_loop,
        args=(memory_getter, interval),
        daemon=True,
        name="consolidation",
    )
    _thread.start()


def stop():
    """停止后台整合线程。"""
    global _running
    _running = False


def is_running() -> bool:
    return _running
