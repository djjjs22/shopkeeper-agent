# -*- coding: utf-8 -*-
"""
pytest 配置文件

这个文件会被 pytest 自动发现和执行，用于全局配置。
不需要显式 import，pytest 框架会自动加载。
"""

import os
import sys
from pathlib import Path

# 把项目根目录加入 sys.path
# 这样测试文件里的 from app.core.sql_safety import ... 才能正常工作
sys.path.insert(0, str(Path(__file__).parent.parent))

# ─────────────────────────────────────────────────────────────────────
# 2026-07-17 改造：LLM Profile 配置需要的环境变量占位符
# 测试环境没有真实 key，设置 dummy 值让配置加载通过（不会被实际调用）
# 字段对齐 conf/app_config.yaml 里的 ${ENV} 占位符（model_name / base_url / api_key）
# ─────────────────────────────────────────────────────────────────────
os.environ.setdefault("LLM_CHEAP_MODEL_NAME", "test-cheap-model")
os.environ.setdefault("LLM_CHEAP_BASE_URL", "https://test-proxy.example.com/v1")
os.environ.setdefault("LLM_CHEAP_API_KEY", "test-cheap-key-not-real")
os.environ.setdefault("LLM_STRONG_MODEL_NAME", "test-strong-model")
os.environ.setdefault("LLM_STRONG_BASE_URL", "https://api.test-strong.example.com/v1")
os.environ.setdefault("LLM_STRONG_API_KEY", "test-strong-key-not-real")
# 老配置兜底（兼容直接读 app_config.llm 的代码路径）
os.environ.setdefault("LLM_API_KEY", "test-legacy-fallback-key")
# admin API 测试用
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
