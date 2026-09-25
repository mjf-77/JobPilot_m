#!/bin/bash
# 在虚机上补全 .env 的部署密钥（每次部署可重复执行）
set -e
cd /root/jobpilot

if [ ! -f .env ]; then
    echo "错误：.env 不存在"
    exit 1
fi

# 移除旧的密钥行，避免重复追加
grep -vE '^(JWT_SECRET|MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD)=' .env > .env.tmp || true
mv .env.tmp .env

# 现场生成随机密钥（不回显到终端）
{
    echo "JWT_SECRET=$(openssl rand -hex 32)"
    echo "MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16)"
    echo "MYSQL_PASSWORD=$(openssl rand -hex 16)"
} >> .env

chmod 600 .env

echo "=== .env 键名（不打印值）==="
cut -d= -f1 .env
echo "=== 总行数 ==="
wc -l < .env
