# MEMORY.md - 长期记忆

## 关于用户
- 姓名：未知（待补充）
- 时区：Asia/Shanghai
- 项目：shopkeeper-agent-main（电商问数AI Agent项目）

## 重要事件
### 2026-06-26
- 创建了 error-learner 技能（自动记录报错+自我成长）
- 成功运行 shopkeeper-agent-main 项目后端
- 项目依赖 Docker 服务未启动，部分功能受限
- **修复并重新启动项目**：
  - 重建了 app/main.py（原文件为空）
  - 修复了 request_id.py 中的拼写错误
  - 修复了中间件注册方式
  - 启动了 Elasticsearch 容器
- **项目现在完全运行**：
  - 后端：http://0.0.0.0:8000
  - 数据库：MySQL (运行中)
  - 向量库：Qdrant (运行中)
  - 搜索：Elasticsearch (运行中)
  - 所有依赖服务正常

## 技能库
- error-learner：五阶段自成长报错处理系统（捕获→诊断→修复→学习→成长）
- 位置：c:\Users\yuanzheng1.zhang\.trae-cn\skills\error-learner\

## 项目配置
- 后端：FastAPI + Uvicorn (http://127.0.0.1:8000)
- 数据库：MySQL (Docker)
- 向量库：Qdrant (Docker)
- 搜索：Elasticsearch (Docker)
- Embedding：需要 Docker 运行（容错跳过）

## 技术决策
- 将 asyncmy 替换为 aiomysql（避免 C++ 编译依赖）
- 实现容错启动（外部服务不可用时仍可运行）
- 配置智谱 API (glm-4-flash)
- 修复了项目中的代码错误

## 待办事项
- 启动 Docker 并运行 `docker compose up -d` ✓（已手动启动所有容器）
- 补充 USER.md 中的用户信息
- 测试完整的电商问数功能（现在可以测试 /api/query 接口）