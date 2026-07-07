"""内存版会话存储 - L1 层"""

from threading import Lock

_session_store: dict= {}
_lock = Lock()

def get_history(session_id:str,max_count:int) -> list:
    history = _session_store.get(session_id,[])
    return history[-max_count:]


def add_message(session_id:str,role:str,content:str) -> None:
    with _lock:
        if session_id not in _session_store:
            _session_store[session_id] = []
        _session_store[session_id].append[{"role":role,"cntent":content}]
        _session_store[session_id] = _session_store[session_id][-10:]

def clear_history(session_id: str) -> None:
    """清空某会话历史"""
    with _lock:
        _session_store.pop(session_id, None)
