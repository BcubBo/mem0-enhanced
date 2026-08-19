# mem0x

自托管 AI 记忆增强服务，基于 mem0ai 构建。

## 特性

- **双端同步**：Qdrant 向量存储 + Neo4j 知识图谱
- **智能搜索**：5维打分 + Rerank 重排序
- **核心记忆**：区分长期稳定记忆和普通记忆
- **自动维护**：过期清理、记忆整合、自进化、反思分析
- **安全防护**：注入防御、PII脱敏、矛盾消解

## 快速开始

### 本地运行

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

### Docker 运行

```bash
# 构建
docker build -t mem0x:0.1.0 .

# 运行
docker run -d \
  -v ~/.mem0x/config.json:/app/config.json \
  -v ~/.mem0x/data:/app/data \
  -p 28768:28768 \
  mem0x:0.1.0
```

## 配置

配置文件位置（按优先级）：
1. 环境变量 `MEM0X_CONFIG`
2. `~/.mem0x/config.json`
3. 项目目录 `config.json`

### 环境变量

| 变量 | 说明 |
|------|------|
| `MEM0X_HOME` | 配置和数据根目录（默认 `~/.mem0x`） |
| `MEM0X_CONFIG` | 配置文件路径 |
| `MEM0X_DATA_DIR` | 数据目录路径 |
| `MEM0X_LLM_API_KEY` | LLM API Key |
| `MEM0X_EMBEDDER_API_KEY` | Embedder API Key |
| `MEM0X_QDRANT_API_KEY` | Qdrant API Key |

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

## 许可证

MIT License
