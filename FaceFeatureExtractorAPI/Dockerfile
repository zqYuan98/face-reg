# 使用轻量级 Python 3.9 基础镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 1. 替换系统源为阿里云镜像 (Debian Bookworm)
# 先清理默认配置，然后写入标准的 sources.list
RUN rm -rf /etc/apt/sources.list.d/* \
    && echo "deb http://mirrors.aliyun.com/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list \
    && echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list \
    && echo "deb http://mirrors.aliyun.com/debian-security/ bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 2. 使用清华源安装 Python 依赖 (加速 pip 安装)
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动服务
CMD ["python", "feature_server.py", "--port", "8000"]
