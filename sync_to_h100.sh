#!/bin/bash
echo "🔄 开始全量同步代码到 H100..."
rsync -avz --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='.DS_Store' ./ root@61.175.246.236:/root/data2/lyn/rj2506_core_system/
echo "✅ 同步完成"
