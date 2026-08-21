# mem0x

自托管 AI 记忆增强服务，基于 mem0ai 构建。

## 特性

- **双端同步**：Qdrant 向量存储 + Neo4j 知识图谱
- **智能搜索**：6维打分（向量+BM25+时间+可靠性+热度+置信度） + Rerank 重排序 + Salience Boost
- **图谱联想召回**：搜索时自动提取实体 → Neo4j 2跳关联查询 → 补充召回
- **矛盾消解**：实体对齐 + 规则收窄，旧记忆自动归档（可回滚）
- **记忆溯源**：写入时携带 sender metadata（sender_open_id, chat_type, chat_id, message_id）
- **核心记忆**：区分长期稳定记忆和普通记忆
- **自动维护**：过期清理、记忆整合、自进化、反思分析
- **版本追踪**：每次更新自动保存历史版本，支持回溯
- **热知识归档**：高频访问的记忆自动升级为核心记忆
- **安全防护**：注入防御（L1-L4）、PII脱敏、Jaccard去重
- **Hermes 集成**：提供 MemoryProvider 插件（prefetch + sync_turn + tool_call）

## 目录结构

```
mem0x/
├── mem0x_server.py        # FastAPI 服务入口
├── mem0x/                 # Hermes 插件
│   ├── __init__.py        # MemoryProvider 实现
│   ├── plugin.yaml        # 插件元数据
│   └── mem0x.json.example # 配置示例
├── wrapper/               # 核心模块
│   ├── mem0_runtime.py    # mem0 运行时
│   ├── auto_expire.py     # 自动过期
│   ├── consolidation.py   # 记忆整合
│   ├── core_memory.py     # 核心记忆
│   ├── evolve_mem.py      # 自进化
│   ├── reflect.py         # 反思引擎
│   ├── neo4j_hook.py      # Neo4j 集成（2跳图谱联想）
│   ├── salience.py        # 显著性引擎
│   ├── graph_export.py    # 图谱导出
│   ├── hot_archive.py     # 热知识归档
│   └── version_tracker.py # 版本追踪
├── security/              # 安全模块
│   ├── pipeline.py        # 安全写入管道
│   ├── scoring.py         # 6维打分
│   ├── conflict_resolver.py # 矛盾消解（实体对齐+规则收窄）
│   ├── dedup.py           # Jaccard 去重
│   ├── injection_guard.py # 注入防御
│   └── self_edit.py       # LLM 语义判重
├── Dockerfile
├── docker-compose.mem0x.yml
├── requirements.txt
└── config.json.example
```

## 快速开始

### 1. Docker 部署（推荐）

```bash
# 克隆
git clone https://github.com/BcubBo/mem0x.git
cd mem0x

# 准备配置
mkdir -p ~/.mem0x/data
cp config-compose.json.example ~/.mem0x/config-compose.json
# 编辑 ~/.mem0x/config-compose.json 填入你的 API key

# 构建并启动
docker build -t mem0xapi:0.1.3 .
docker compose -f docker-compose.mem0x.yml up -d

# 验证
curl http://localhost:28768/health
```

### 2. 本地运行

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.json.example config.json
# 编辑 config.json 填入你的 API key

python mem0x_server.py
```

## 配置

### 配置文件优先级

1. 环境变量 `MEM0X_CONFIG`
2. `~/.mem0x/config.json`（本地运行）
3. `~/.mem0x/config-compose.json`（Docker 运行，挂载到容器内 `/app/config.json`）
4. 项目目录 `config.json`

### Docker 网络配置

Docker 部署时，服务地址必须使用 Docker 网络名称：

```json
{
  "mem0": {
    "vector_store": {
      "config": {
        "url": "http://qdrant:6333"  // ✅ Docker DNS
        // "url": "http://127.0.0.1:26333"  // ❌ 容器内不通
      }
    }
  },
  "neo4j": {
    "uri": "bolt://neo4j:7687"  // ✅ Docker DNS
    // "uri": "bolt://localhost:26787"  // ❌ 容器内不通
  },
  "server": {
    "host": "0.0.0.0",  // ✅ Docker 需要绑定所有接口
    // "host": "127.0.0.1"  // ❌ Docker 端口映射不通
  }
}
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `MEM0X_CONFIG` | 配置文件路径 |
| `MEM0X_HOME` | 配置和数据根目录（默认 `~/.mem0x`） |

## API 端点

### 核心
| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/add` | 写入记忆（含注入防御+去重+矛盾消解） |
| POST | `/search` | 搜索记忆（向量+Neo4j联想+salience boost+rerank） |
| POST | `/delete` | 删除记忆（软删除） |
| POST | `/update` | 更新记忆（双端同步） |

### 监控
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（mem0+neo4j状态） |
| GET | `/stats` | 数据统计 |
| GET | `/degradation` | 降级状态 |

### 维护
| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/expire` | 过期清理 |
| POST | `/consolidate` | 记忆整合（碎片合并） |
| POST | `/evolve` | 自进化 |
| POST | `/reflect` | 系统反思 |

### 核心记忆
| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/core-memory/add` | 标记为核心记忆 |
| POST | `/core-memory/remove` | 移除核心标记 |
| GET | `/core-memory/list` | 列出核心记忆 |

### 版本追踪
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/versions/{memory_id}` | 查询记忆版本历史 |
| GET | `/versions/stats` | 版本统计 |

### 热知识归档
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/archive/candidates` | 查询归档候选 |
| POST | `/archive/run` | 手动触发归档 |
| GET | `/archive/status` | 归档线程状态 |

### 图谱可视化
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/graph/export` | 导出知识图谱（节点+边） |

## Hermes 插件部署

### 安装

```bash
# 复制插件到 Hermes profile
cp -r mem0x/ ~/.hermes/profiles/your-profile/plugins/mem0x/

# 复制配置
cp mem0x/mem0x.json.example ~/.hermes/profiles/your-profile/mem0x.json
# 编辑 mem0x.json 设置 service_url（指向 mem0x API 服务地址）
```

### 配置 (mem0x.json)

```json
{
  "service_url": "http://127.0.0.1:28768",
  "user_id": "your-user-id",
  "agent_id": "hermes"
}
```

### 启用

在 `config.yaml` 中设置：

```yaml
memory:
  memory_enabled: true
  provider: mem0x
```

### 插件功能

| 功能 | 说明 |
|------|------|
| `prefetch()` | 对话前预取记忆，注入 system prompt（含 Neo4j 图谱联想） |
| `sync_turn()` | 对话后异步写入记忆（含 sender metadata 溯源） |
| `handle_tool_call()` | 工具调用时的 add/search/update/delete |

## 数据存储

```
~/.mem0x/
├── config-compose.json   # Docker 配置
├── config.json           # 本地配置
└── data/                 # SQLite 数据
    ├── conflict.db       # 矛盾消解记录
    ├── core_memory.db    # 核心记忆元数据
    ├── reflect.db        # 反思日志
    ├── salience.db       # 热度/显著性追踪
    └── version_history.db # 版本历史
```

向量和图谱存储：
- **Qdrant**：向量索引（端口 26333）
- **Neo4j**：实体关系图谱（bolt 26787 / HTTP 27474）

## 开发

```bash
# 测试环境（端口 28767）
python mem0x_server.py  # config.json 中 port 设为 28767

# 测试环境验证
curl http://localhost:28767/health
curl -X POST http://localhost:28767/search -d '{"query":"test","limit":1}'
```

## 许可证

MIT License
