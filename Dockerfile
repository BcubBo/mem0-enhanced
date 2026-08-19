FROM python:3.12-slim

# 使用清华镜像源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY wrapper/ ./wrapper/
COPY security/ ./security/
COPY api_server.py .

# 创建数据目录
RUN mkdir -p /app/data

EXPOSE 28768

# 环境变量
ENV MEM0X_HOME=/app
ENV MEM0X_CONFIG=/app/config.json
ENV MEM0X_DATA_DIR=/app/data

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:28768/health || exit 1

CMD ["python", "api_server.py"]
