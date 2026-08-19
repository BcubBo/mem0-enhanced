# mem0x

自托管 AI 记忆增强服务，基于 mem0ai 构建。

## 特性

- **双端同步**：Qdrant 向量存储 + Neo4j 知识图谱
- **智能搜索**：5维打分 + Rerank 重排序
- **核心记忆**：区分长期稳定记忆和普通记忆
- **自动维护**：过期清理、记忆整合、自进化、反思分析
- **安全防护**：注入防御、PII脱敏、矛盾消解
- **Hermes 集成**：提供 MemoryProvider 插件

## 目录结构

```
mem0x/
├── api_server.py          # FastAPI 服务入口
├── wrapper/               # 核心模块
│   ├── mem0_runtime.py    # mem0 运行时
│   ├── auto_expire.py     # 自动过期
│   ├── consolidation.py   # 记忆整合
│   ├── core_memory.py     # 核心记忆
│   ├── evolve_mem.py      # 自进化
│   ├── reflect.py         # 反思引擎
│   ├── neo4j_hook.py      # Neo4j 集成
│   └── salience.py        # 显著性引擎
├── security/              # 安全模块
│   ├── pipeline.py        # 安全写入管道
│   ├── scoring.py         # 5维打分
│   ├── conflict_resolver.py
│   ├── dedup.py
│   ├── injection_guard.py
│   └── self_edit.py
├── plugin/                # Hermes 插件
│   ├── __init__.py
│   ├── plugin.yaml
│   └── mem0x.json.example
├── Dockerfile
├── requirements.txt
└── config.json.example
```

## 快速开始

### 1. 本地运行

```bash
# 克隆
git clone https://github.com/BcubBo/mem0x.git
cd mem0x

# 创建虚拟环境
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配置
mkdir -p ~/.mem0x
cp config.json.example ~/.mem0x/config.json
# 编辑 ~/.mem0x/config.json 填入你的 API key

# 运行
python api_server.py
```

### 2. Docker 运行

```bash
# 构建
docker build -t mem0x:0.1.0 .

# 运行
docker run -d \
  --name mem0x \
  -v ~/.mem0x/config.json:/app/config.json \
  -v ~/.mem0x/data:/app/data \
  -p 8080:8080 \
  mem0x:0.1.0
```

### 3. Hermes 插件安装

```bash
# 复制插件到 Hermes
cp -r plugin/ ~/.hermes/profiles/your-profile/plugins/mem0x/

# 复制配置
cp plugin/mem0x.json.example ~/.hermes/profiles/your-profile/mem0x.json
# 编辑 mem0x.json 设置 service_url

# 在 config.yaml 中启用
# memory.provider: mem0x
```

## 配置

### 服务配置 (config.json)

配置文件位置（按优先级）：
1. 环境变量 `MEM0X_CONFIG`
2. `~/.mem0x/config.json`
3. 项目目录 `config.json`

```json
{
  "mem0": {
    "llm": {
      "provider": "openai",
      "config": {
        "model": "Qwen/Qwen2.5-14B-Instruct",
        "api_key": "sk-your-llm-api-key",
        "openai_base_url": "https://api.siliconflow.cn/v1"
      }
    },
    "embedder": {
      "provider": "openai",
      "config": {
        "model": "BAAI/bge-m3",
        "api_key": "sk-your-embedder-api-key",
        "openai_base_url": "https://api.siliconflow.cn/v1"
      }
    },
    "vector_store": {
      "provider": "qdrant",
      "config": {
        "url": "http://your-qdrant-host:6333",
        "api_key": "your-qdrant-api-key",
        "embedding_model_dims": 1024,
        "collection_name": "mem0"
      }
    }
  },
  "rerank": {
    "provider": "siliconflow",
    "config": {
      "model": "BAAI/bge-reranker-v2-m3",
      "api_key": "sk-your-rerank-api-key",
      "openai_base_url": "https://api.siliconflow.cn/v1"
    }
  },
  "neo4j": {
    "enabled": true,
    "uri": "bolt://your-neo4j-host:7687",
    "username": "neo4j",
    "password": "your-neo4j-password"
  },
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  }
}
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `MEM0X_HOME` | 配置和数据根目录（默认 `~/.mem0x`） |
| `MEM0X_CONFIG` | 配置文件路径 |
| `MEM0X_DATA_DIR` | 数据目录路径 |

## API 端点

### 核心
- `POST /add` - 写入记忆
- `POST /search` - 搜索记忆
- `POST /delete` - 删除记忆
- `POST /update` - 更新记忆

### 监控
- `GET /health` - 健康检查
- `GET /stats` - 数据统计
- `GET /degradation` - 降级状态

### 维护
- `POST /expire` - 过期清理
- `POST /consolidate` - 记忆整合
- `POST /evolve` - 自进化
- `POST /reflect` - 系统反思

### 核心记忆
- `POST /core-memory/add` - 标记为核心记忆
- `POST /core-memory/remove` - 移除核心标记
- `GET /core-memory/list` - 列出核心记忆

## 数据存储

```
~/.mem0x/
├── config.json          # 配置文件
└── data/                # SQLite 数据
    ├── conflict.db      # 冲突记录
    ├── core_memory.db   # 核心记忆
    ├── reflect.db       # 反思日志
    └── salience.db      # 热度追踪
```

记忆内容存储在：
- **Qdrant**：向量索引
- **Neo4j**：实体关系图谱

## 开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
python -m pytest tests/

# 代码检查
python -m ruff check .
```

## 许可证

MIT License
