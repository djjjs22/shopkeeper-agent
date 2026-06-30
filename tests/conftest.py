# -*- coding: utf-8 -*-
"""
pytest 配置文件

这个文件会被 pytest 自动发现和执行，用于全局配置。
不需要显式 import，pytest 框架会自动加载。
"""

import sys
from pathlib import Path

# 把项目根目录加入 sys.path
# 这样测试文件里的 from app.core.sql_safety import ... 才能正常工作
sys.path.insert(0, str(Path(__file__).parent.parent))
