# -*- coding: utf-8 -*-
"""
会话签名工具（2026-07-20 #4）

防 session fixation / session_id 伪造：
- 客户端发来的 session_id cookie 必须带 HMAC 签名，格式：`<uuid>.<sig>`
- 服务端用 SESSION_SECRET 算 HMAC 校验，签名不匹配 / 被篡改 → 视为非法
- 非法时不直接拒（不破坏 UX），而是发新的合法 session_id

设计权衡：
- HMAC 让攻击者无法构造合法的 session_id，但不能阻止重放（同一个 signed id
  被偷走仍可用）。重放防护需要 SameSite cookie + HTTPS，已在 #4 一并修。
- session 内容（消息历史）存 Redis，server-side 不存敏感数据，session_id
  本身只是 key；即使被伪造也只是污染 Redis key，无法读到他人数据。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Optional

# 服务端密钥：优先环境变量，缺失时用一个进程级随机值（每次重启变化，
# 等于所有现有 session 失效——可接受，重启本来就罕见）
_SESSION_SECRET: Optional[bytes] = None


def _get_secret() -> bytes:
    global _SESSION_SECRET
    if _SESSION_SECRET is None:
        env_val = os.environ.get("SESSION_SECRET", "")
        if env_val:
            _SESSION_SECRET = env_val.encode("utf-8")
        else:
            # 进程级随机：本地开发不需要配置也能跑，但生产强烈建议设 SESSION_SECRET
            _SESSION_SECRET = os.urandom(32)
    return _SESSION_SECRET


def _sign(payload: str) -> str:
    """对 payload（如 uuid 字符串）算 HMAC-SHA256，返回 hex 前 16 字符"""
    return hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def issue_session_id() -> str:
    """生成新的合法 session_id（uuid + 签名）

    格式：`<uuid4>.<sig_hex_16>`
    """
    raw = str(uuid.uuid4())
    return f"{raw}.{_sign(raw)}"


def verify_session_id(signed: Optional[str]) -> bool:
    """校验 session_id 是否合法签名

    - None / 非字符串 / 格式错 → False
    - 签名不匹配 → False（可能被篡改或来自其他部署）
    - 合法 → True
    """
    if not signed or not isinstance(signed, str) or "." not in signed:
        return False
    try:
        raw, sig = signed.rsplit(".", 1)
    except ValueError:
        return False
    if not raw or not sig:
        return False
    # hmac.compare_digest 防时序攻击
    return hmac.compare_digest(_sign(raw), sig)
