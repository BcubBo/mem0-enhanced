"""neo4j_hook — Neo4j 知识图谱集成（standalone 版）

写入：提取实体+关系 → 写入 Neo4j（带 source_memory_id）
删除：按 source_memory_id 精确清理
查询：提取查询实体 → 关联查询 → 返回关联结果
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bMem0X.neo4j")

# Neo4j 驱动（可选）
try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

# ── 预编译正则 ──
RE_EN_ENTITY = re.compile(r'\b[A-Z][a-zA-Z0-9_-]{2,}\b')
RE_ZH_ENTITY = re.compile(r'[\u4e00-\u9fa5]{2,4}')
ZH_STOP_WORDS = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "那", "被",
    "把", "让", "给", "对", "从", "为", "以", "但", "而", "如果",
    "因为", "所以", "这个", "那个", "什么", "怎么", "可以", "已经",
})

# ── 关系类型白名单 ──
RELATION_TYPE_MAP = {
    "管理": "MANAGES", "使用": "USES", "依赖": "DEPENDS_ON",
    "配置": "CONFIGURED_BY", "包含": "CONTAINS", "属于": "BELONGS_TO",
}
ALLOWED_REL_TYPES = frozenset(RELATION_TYPE_MAP.values()) | {"RELATED"}
ALLOWED_ENTITY_TYPES = frozenset({"Entity", "Person", "Project", "Service", "Config", "Module", "Tool", "Concept"})

# ── 资源限制 ──
MAX_WRITE_ENTITIES = 20
MAX_WRITE_RELATIONS = 100
MAX_QUERY_ENTITIES = 20
MAX_RELATED_PER_ENTITY = 20
MAX_EXTRA_TEXTS = 50
MAX_TEXT_LEN = 2000

# ── 已知实体类型 ──
_PERSON_NAMES: frozenset = frozenset()
_KNOWN_PROJECTS = frozenset({"lark-hls-v2", "mem0-enhanced", "伏魔记", "hermes", "玄铁"})
_KNOWN_SERVICES = frozenset({"Qdrant", "Neo4j", "Gateway", "飞书", "Feishu", "Caddy", "Prometheus", "Grafana"})
_KNOWN_TOOLS = frozenset({"git", "docker", "hermes", "curl", "pytest"})


def load_known_entities(config: dict) -> None:
    """从 config.json 的 known_persons 字段加载人名列表（不硬编码在源码中）。"""
    global _PERSON_NAMES
    names = config.get("known_persons", [])
    if names:
        _PERSON_NAMES = frozenset(names)
        logger.info("neo4j: loaded %d known persons", len(_PERSON_NAMES))


def _guess_type(name: str) -> str:
    if name in _PERSON_NAMES:
        return "Person"
    if name in _KNOWN_PROJECTS:
        return "Project"
    if name in _KNOWN_SERVICES:
        return "Service"
    if name.lower() in _KNOWN_TOOLS:
        return "Tool"
    if name.endswith((".json", ".yaml", ".yml", ".toml", ".conf")):
        return "Config"
    if name.endswith((".py", ".js", ".ts", ".rs")):
        return "Module"
    return "Entity"


def _sanitize_name(name: str, max_len: int = 200) -> str:
    import unicodedata
    name = ''.join(c for c in name if unicodedata.category(c) != 'Cc')
    name = re.sub(r'[{}\[\]|*`$\\\"\'"]', '', name)
    name = re.sub(r'[\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\u3010\u3011]', '', name)
    name = name.strip()
    return name[:max_len]


# ── PII 实体过滤 ──
_PII_PATTERNS = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"  # 身份证
    r"|(?<!\d)1[3-9]\d{9}(?!\d)"       # 手机号
    r"|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"  # 邮箱
)


def _is_pii_entity(name: str) -> bool:
    """检查实体名是否包含 PII 信息。"""
    return bool(_PII_PATTERNS.search(name))


# 已知真实姓名 → 脱敏名称映射（从 config.json 加载，不硬编码）
_PII_NAME_MAP: dict[str, str] = {}


def load_redact_names(config: dict) -> None:
    """从 config.json 的 redact_names 字段加载脱敏映射。"""
    global _PII_NAME_MAP
    _PII_NAME_MAP = dict(config.get("redact_names", {}))
    if _PII_NAME_MAP:
        logger.info("neo4j: loaded %d redact names", len(_PII_NAME_MAP))


def _redact_entity_name(name: str) -> str:
    """脱敏实体名中的真实姓名。"""
    for real, fake in _PII_NAME_MAP.items():
        if real in name:
            name = name.replace(real, fake)
    return name


def _extract_entities(text: str) -> Dict[str, List]:
    """规则提取实体和关系。"""
    entities = []
    relations = []
    seen = set()

    for match in RE_EN_ENTITY.findall(text):
        if match not in seen:
            seen.add(match)
            entities.append({"name": match, "type": _guess_type(match)})

    for match in RE_ZH_ENTITY.findall(text):
        if match not in ZH_STOP_WORDS and match not in seen:
            seen.add(match)
            entities.append({"name": match, "type": "Entity"})

    relation_patterns = [
        (r'(.{2,8})(管理|负责|主导|创建了?)\s*(.{2,20})', 'MANAGES'),
        (r'(.{2,8})(使用|用|采用|基于)\s*(.{2,20})', 'USES'),
        (r'(.{2,8})(依赖|需要|基于)\s*(.{2,20})', 'DEPENDS_ON'),
        (r'(.{2,8})(包含|包括|有)\s*(.{2,20})', 'CONTAINS'),
        (r'(.{2,8})(配置|设置)\s*(.{2,20})', 'CONFIGURED_BY'),
        (r'(.{2,8})(是|属于)\s*(.{2,20})', 'RELATED'),
    ]

    for pattern, rel_type in relation_patterns:
        for m in re.finditer(pattern, text):
            src = m.group(1).strip()
            obj = m.group(3).strip()
            src = re.sub(r'[，。、；：""''！？\s]+', '', src)
            obj = re.sub(r'[，。、；：""''！？\s]+', '', obj)
            if len(src) >= 2 and len(obj) >= 2 and src != obj:
                relations.append({"from": src, "to": obj, "type": rel_type})
                for name in (src, obj):
                    if name not in seen:
                        seen.add(name)
                        entities.append({"name": name, "type": _guess_type(name)})

    return {"entities": entities, "relations": relations}


class Neo4jHook:
    """Neo4j 知识图谱 hook（standalone 版）。"""

    def __init__(self):
        self._driver = None
        self._enabled = False
        self._load_config()

    def _load_config(self) -> None:
        if not NEO4J_AVAILABLE:
            logger.debug("neo4j: package not installed")
            return

        try:
            from security.utils import get_config
            config = get_config()
            neo4j_config = config.get("neo4j", {})

            if not neo4j_config.get("enabled", False):
                logger.debug("neo4j: disabled")
                return

            uri = neo4j_config.get("uri", "bolt://localhost:26787")
            username = neo4j_config.get("username", "neo4j")
            password = neo4j_config.get("password", "")

            self._driver = GraphDatabase.driver(uri, auth=(username, password))
            self._enabled = True
            logger.debug("neo4j: enabled (uri=%s)", uri)

        except Exception as e:
            logger.warning("neo4j: config load failed: %s", e)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def write(self, memory_id: str, text: str) -> None:
        """写入记忆后，提取实体+关系写入 Neo4j（带 source_memory_id）。

        PII 过滤：跳过含身份证/手机/邮箱的实体，脱敏真实姓名。
        """
        if not self._enabled or not self._driver:
            return

        extraction = _extract_entities(text)
        entities = extraction.get("entities", [])
        relations = extraction.get("relations", [])

        if not entities:
            return

        if len(entities) > MAX_WRITE_ENTITIES:
            entities = entities[:MAX_WRITE_ENTITIES]

        # PII 过滤：跳过含敏感信息的实体，脱敏真实姓名
        filtered_entities = []
        for entity in entities:
            name = entity.get("name", "")
            if _is_pii_entity(name):
                logger.debug("neo4j: skip PII entity: %s", name[:20])
                continue
            entity["name"] = _redact_entity_name(name)
            filtered_entities.append(entity)
        entities = filtered_entities

        if not entities:
            return

        # 写入实体（带 source_memory_id）
        with self._driver.session() as session:
            for entity in entities:
                name = _sanitize_name(entity.get("name", ""))
                etype = entity.get("type", "Entity")
                if name:
                    if etype not in ALLOWED_ENTITY_TYPES:
                        etype = "Entity"
                    try:
                        session.run(
                            "MERGE (n {name: toLower($name)}) "
                            "SET n:{etype}, n.original_name = $name, "
                            "n.source_memory_id = CASE "
                            "  WHEN n.source_memory_id IS NULL THEN $mid "
                            "  ELSE n.source_memory_id + ',' + $mid END",
                            name=name, etype=etype, mid=memory_id,
                        )
                    except Exception as e:
                        logger.debug("neo4j: entity creation failed: %s", e)

        if len(relations) > MAX_WRITE_RELATIONS:
            relations = relations[:MAX_WRITE_RELATIONS]

        if relations:
            with self._driver.session() as session:
                for rel in relations:
                    from_name = _sanitize_name(rel.get("from", ""))
                    to_name = _sanitize_name(rel.get("to", ""))
                    rel_type = rel.get("type", "RELATED")
                    if from_name and to_name and rel_type in ALLOWED_REL_TYPES:
                        try:
                            session.run(
                                f"MATCH (a {{name: toLower($from_name)}}), (b {{name: toLower($to_name)}}) "
                                f"MERGE (a)-[r:{rel_type}]->(b)",
                                from_name=from_name, to_name=to_name,
                            )
                        except Exception as e:
                            logger.debug("neo4j: relation creation failed: %s", e)

        logger.debug("neo4j: write %d entities, %d relations for %s", len(entities), len(relations), memory_id[:8])

    def cleanup(self, memory_id: str) -> None:
        """删除记忆后，清理关联的 Neo4j 实体。"""
        if not self._enabled or not self._driver:
            return

        with self._driver.session() as session:
            # 删除 source_memory_id 包含该 memory_id 的孤立实体
            try:
                result = session.run(
                    "MATCH (n) WHERE n.source_memory_id CONTAINS $mid "
                    "AND NOT (n)--() "
                    "WITH n LIMIT 100 "
                    "REMOVE n.source_memory_id "
                    "DELETE n "
                    "RETURN count(n) AS deleted",
                    mid=memory_id,
                )
                deleted = result.single()["deleted"]
                if deleted > 0:
                    logger.debug("neo4j: cleaned up %d orphan entities for %s", deleted, memory_id[:8])
            except Exception as e:
                logger.debug("neo4j: cleanup failed: %s", e)

    def query(self, query: str, extra_texts: list = None) -> List[Dict[str, Any]]:
        """查询 Neo4j 关联实体。"""
        if not self._enabled or not self._driver:
            return []

        all_texts = [query[:MAX_TEXT_LEN]]
        if extra_texts:
            for t in extra_texts[:MAX_EXTRA_TEXTS]:
                if isinstance(t, str) and t:
                    all_texts.append(t[:MAX_TEXT_LEN])

        combined_text = " ".join(all_texts)
        extraction = _extract_entities(combined_text)
        seen_query = set()
        query_entities = []
        for e in extraction.get("entities", []):
            n = _sanitize_name(e.get("name", ""))
            if n and n not in seen_query:
                seen_query.add(n)
                query_entities.append(n)

        if not query_entities:
            return []

        query_entities = query_entities[:MAX_QUERY_ENTITIES]

        results = []
        with self._driver.session() as session:
            for entity_name in query_entities:
                try:
                    result = session.run(
                        "MATCH (n {name: toLower($name)}) "
                        "OPTIONAL MATCH (n)-[r]-(related) "
                        "RETURN n.name AS name, n.original_name AS original_name, "
                        "labels(n) AS labels, "
                        f"collect(DISTINCT {{type: type(r), name: related.name}})[..{MAX_RELATED_PER_ENTITY}] AS relations",
                        name=entity_name,
                    )
                    for record in result:
                        name = record.get("original_name") or record["name"]
                        labels = record["labels"]
                        relations = record["relations"]
                        rel_parts = []
                        for r in relations:
                            if r["name"] and r["type"] in ALLOWED_REL_TYPES:
                                rel_parts.append(f"{r['name']}({r['type']})")
                        rel_str = ", ".join(rel_parts)
                        results.append({
                            "name": name,
                            "label": labels[0] if labels else "Unknown",
                            "relations": rel_str,
                        })
                except Exception as e:
                    logger.debug("neo4j: query failed: %s", e)

        return results

    def shutdown(self) -> None:
        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None


# 全局单例
_hook_instance: Optional[Neo4jHook] = None
_hook_lock = threading.Lock()


def get_hook() -> Neo4jHook:
    global _hook_instance
    if _hook_instance is None:
        with _hook_lock:
            if _hook_instance is None:
                _hook_instance = Neo4jHook()
    return _hook_instance
