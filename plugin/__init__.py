"""mem0x — Hermes MemoryProvider 插件

通过 HTTP 调用 mem0x 独立服务。
只用标准库 urllib，不给宿主装依赖。

部署：~/.hermes/profiles/bo/plugins/mem0x/
配置：memory.provider: mem0x
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes_plugins.mem0x")

# 尝试导入 lark-hls-v2 的 contextvars，用于获取 sender 信息
_msg_ctx = None
try:
    from hermes_plugins.lark_hls_v2.interceptors import _msg_ctx as _lark_msg_ctx
    _msg_ctx = _lark_msg_ctx
except ImportError:
    logger.debug("mem0x: lark-hls-v2 not available, sender context disabled")

# 全局变量，用于在 sync_turn 时保存 sender 信息
_sender_context_cache: Dict[str, str] = {}


def _get_sender_context() -> Dict[str, str]:
    """从 lark-hls-v2 的 _msg_ctx 读取 sender 信息。"""
    global _sender_context_cache
    # 优先从缓存读取（sync_turn 场景）
    if _sender_context_cache:
        logger.info("[mem0x-debug] _get_sender_context: from cache=%s", _sender_context_cache)
        return dict(_sender_context_cache)
    if _msg_ctx is None:
        logger.info("[mem0x-debug] _get_sender_context: _msg_ctx is None")
        return {}
    ctx = _msg_ctx.get(None)
    if not ctx:
        logger.info("[mem0x-debug] _get_sender_context: ctx is None/empty")
        return {}
    result = {
        "sender_open_id": ctx.get("user_id", ""),  # 用 sender_open_id 避免被 mem0 提取为顶层 user_id
        "user_name": ctx.get("user_name", ""),
        "chat_id": ctx.get("chat_id", ""),
        "chat_type": ctx.get("chat_type", "dm"),
        "message_id": ctx.get("message_id", ""),
        "platform": ctx.get("platform", ""),
    }
    # 自动保存到缓存，供 sync_turn 后台线程使用
    _sender_context_cache = dict(result)
    logger.info("[mem0x-debug] _get_sender_context: from ctxvar=%s (cached)", result)
    return result


def _set_sender_context_cache(ctx: Dict[str, str]) -> None:
    """设置 sender 上下文缓存（由 lark-hls-v2 调用）。"""
    global _sender_context_cache
    _sender_context_cache = {
        "sender_open_id": ctx.get("user_id", ""),
        "user_name": ctx.get("user_name", ""),
        "chat_id": ctx.get("chat_id", ""),
        "chat_type": ctx.get("chat_type", "dm"),
        "message_id": ctx.get("message_id", ""),
        "platform": ctx.get("platform", ""),
    }
    logger.info("[mem0x-debug] _set_sender_context_cache: cache=%s", _sender_context_cache)


# ═══════════════════════════════════════════════════
# HTTP 客户端（零依赖）
# ═══════════════════════════════════════════════════

class _Client:
    """轻量 HTTP 客户端（urllib，零依赖）。"""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def request(self, method: str, path: str, body: Any = None, timeout: float = 6.0) -> Any:
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def try_request(self, method: str, path: str, **kwargs) -> Optional[Any]:
        """失败返回 None，不让对话崩。"""
        try:
            return self.request(method, path, **kwargs)
        except Exception as e:
            logger.debug("mem0x request failed: %s", e)
            return None


_client: Optional[_Client] = None
_config: Optional[dict] = None


def _load_config() -> dict:
    """从 mem0x.json 加载配置。"""
    global _config
    if _config is not None:
        return _config
    try:
        config_path = os.path.join(
            os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")),
            "mem0x.json",
        )
        with open(config_path) as f:
            _config = json.load(f)
    except Exception as e:
        logger.debug("mem0x: failed to load config: %s, using defaults", e)
        _config = {}
    return _config


def _get_client() -> _Client:
    global _client
    if _client is None:
        cfg = _load_config()
        url = cfg.get("service_url", "http://127.0.0.1:28768")
        _client = _Client(url)
    return _client


def _get_user_id() -> str:
    return _load_config().get("user_id", "yang")


def _get_agent_id() -> str:
    return _load_config().get("agent_id", "hermes")


# ═══════════════════════════════════════════════════
# MemoryProvider 接口实现
# ═══════════════════════════════════════════════════

class Mem0RemoteProvider:
    """bo-mem0-enhanced MemoryProvider（HTTP 远程调用）。"""

    name = "mem0x"

    def __init__(self, config: dict = None):
        self._config = config or {}

    def is_available(self) -> bool:
        """检查服务是否可用。"""
        client = _get_client()
        result = client.try_request("GET", "/health", timeout=2.0)
        return result is not None and result.get("status") in ("ok", "degraded")

    def initialize(self, session_id: str = "", **kwargs) -> None:
        """初始化（无操作，服务端已初始化）。"""
        pass

    def on_session_end(self, messages: list, **kwargs) -> None:
        """会话结束时的回调（空实现）。"""
        pass

    def system_prompt_block(self) -> str:
        """系统提示词注入。"""
        return ""

    def prefetch(self, query: str, session_id: str = "", **kwargs) -> str:
        """预取记忆（注入 system prompt）。"""
        client = _get_client()
        sender = _get_sender_context()
        body = {
            "query": query,
            "limit": 5,
            "rerank": True,
            "metadata": sender if sender else None,
        }
        result = client.try_request("POST", "/search", body=body, timeout=6.0)
        if not result:
            return ""

        results = result.get("results", [])
        if not results:
            return ""

        lines = []
        for r in results:
            mem = r.get("memory", "")
            score = r.get("score", 0)
            if mem:
                lines.append(f"- {mem} (score: {score:.2f})")
        return "\n".join(lines)

    def sync_turn(self, user_msg: str, assistant_msg: str, session_id: str = "", **kwargs) -> None:
        """对话后异步写入记忆。"""
        def _write():
            client = _get_client()
            sender = _get_sender_context()
            content = f"User: {user_msg}\nAssistant: {assistant_msg}"
            body = {
                "messages": content,
                "user_id": _get_user_id(),
                "agent_id": _get_agent_id(),
                "infer": True,
                "metadata": sender if sender else None,
            }
            logger.info("[mem0x-debug] sync_turn: sender=%s, metadata=%s", sender, body.get("metadata"))
            client.try_request("POST", "/add", body=body, timeout=20.0)

        threading.Thread(target=_write, daemon=True).start()

    def on_pre_compress(self, messages: list, **kwargs) -> Optional[str]:
        """压缩前抢救。"""
        recent = []
        for msg in messages[-10:]:
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                recent.append(msg)
        if not recent:
            return None

        def _write():
            client = _get_client()
            content = "\n".join(
                f"{m['role']}: {m.get('content', '')}" for m in recent
            )
            client.try_request("POST", "/add", body={
                "messages": content,
                "user_id": _get_user_id(),
                "agent_id": _get_agent_id(),
                "infer": True,
            }, timeout=20.0)

        threading.Thread(target=_write, daemon=True).start()
        return None

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict = None, **kwargs) -> None:
        """MEMORY.md 写入后镜像。"""
        if not content or len(content) < 10:
            return

        def _write():
            client = _get_client()
            client.try_request("POST", "/add", body={
                "messages": content,
                "user_id": _get_user_id(),
                "agent_id": _get_agent_id(),
                "infer": True,
                "metadata": {"source": "MEMORY.md", "action": action},
            }, timeout=20.0)

        threading.Thread(target=_write, daemon=True).start()

    def get_tool_schemas(self) -> List[dict]:
        """返回工具 schema。"""
        return [ADD_SCHEMA, SEARCH_SCHEMA, DELETE_SCHEMA, UPDATE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict) -> str:
        """处理工具调用。返回 JSON 字符串。"""
        client = _get_client()
        sender = _get_sender_context()

        if tool_name == "mem0_add":
            content = args.get("content", "")
            body = {
                "messages": content,
                "user_id": _get_user_id(),
                "agent_id": _get_agent_id(),
                "infer": False,
                "metadata": sender if sender else None,
            }
            result = client.try_request("POST", "/add", body=body, timeout=20.0)

        elif tool_name == "mem0_search":
            query = args.get("query", "")
            top_k = args.get("top_k", 10)
            body = {
                "query": query,
                "limit": top_k,
                "rerank": True,
                "metadata": sender if sender else None,
            }
            result = client.try_request("POST", "/search", body=body, timeout=6.0)

        elif tool_name == "mem0_delete":
            memory_id = args.get("memory_id", "")
            confirm = args.get("confirm", False)
            # confirm=True → 硬删除（/delete/confirm）
            # confirm=False → 软删除（/delete），标记 deleted_at
            endpoint = "/delete/confirm" if confirm else "/delete"
            result = client.try_request("POST", endpoint, body={
                "memory_id": memory_id,
            }, timeout=6.0)

        elif tool_name == "mem0_update":
            memory_id = args.get("memory_id", "")
            content = args.get("content", "")
            result = client.try_request("POST", "/update", body={
                "memory_id": memory_id,
                "content": content,
            }, timeout=6.0)

        else:
            result = None

        if result is None:
            return json.dumps({"error": "Request failed or returned None"})
        return json.dumps(result) if isinstance(result, dict) else str(result)

    def shutdown(self) -> None:
        """关闭（无操作）。"""
        pass


# ═══════════════════════════════════════════════════
# 工具 Schema
# ═══════════════════════════════════════════════════

ADD_SCHEMA = {
    "name": "mem0_add",
    "description": "存储持久事实到长期记忆。",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要存储的事实内容"},
        },
        "required": ["content"],
    },
}

SEARCH_SCHEMA = {
    "name": "mem0_search",
    "description": "搜索长期记忆。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询"},
            "top_k": {"type": "integer", "description": "返回结果数量", "default": 10},
        },
        "required": ["query"],
    },
}

DELETE_SCHEMA = {
    "name": "mem0_delete",
    "description": "删除长期记忆。默认软删除（标记 deleted_at），confirm=True 硬删除。",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "记忆 ID"},
            "confirm": {"type": "boolean", "description": "是否硬删除（默认 false=软删除）", "default": False},
        },
        "required": ["memory_id"],
    },
}

UPDATE_SCHEMA = {
    "name": "mem0_update",
    "description": "更新长期记忆内容。",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "记忆 ID"},
            "content": {"type": "string", "description": "新内容"},
        },
        "required": ["memory_id", "content"],
    },
}


# ═══════════════════════════════════════════════════
# 注册入口（Hermes 插件系统调用）
# ═══════════════════════════════════════════════════

_provider: Optional[Mem0RemoteProvider] = None


def register(ctx) -> None:
    """Hermes 插件注册入口。

    ctx 是 _ProviderCollector 实例，调用 ctx.register_memory_provider() 注册。
    激活方式：config.yaml 中设置 memory.provider: mem0x
    """
    global _provider
    _provider = Mem0RemoteProvider()
    ctx.register_memory_provider(_provider)
    logger.info("mem0x plugin registered via ctx.register_memory_provider()")


def get_provider() -> Mem0RemoteProvider:
    """获取 provider 实例。"""
    global _provider
    if _provider is None:
        _provider = Mem0RemoteProvider()
    return _provider
