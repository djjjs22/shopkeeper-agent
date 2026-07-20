"""
关键词抽取节点

负责从用户自然语言问题中识别检索线索
后续字段召回 字段取值召回和指标召回都会基于这些关键词展开

2026-07-14 改造：
  改前：rewrite_query 把"2025-12-01至2025-12-31华北销售额"覆盖到 state['query']，
       本节点把整句也作为兜底关键词加进列表，污染 Qdrant/ES 召回。
  改后：state['query'] 永远是用户原句，本节点只切原句 + 只保留 jieba 切出的关键词。
       时间信息在 state['time_range'] 单独存，不影响关键词抽取。

2026-07-20 改造（#16）：合并版 LLM 关键词扩展
  改前：三个 recall 节点（column / value / metric）各自调一次 LLM 做扩展，
       共 3 次调用，输入都是 query，重复读同一句。
  攅后：本节点 jieba 抽完后，额外做一次 LLM 扩展，产出三维度 dict 写入 state。
       三个 recall 节点直接读 state['extended_keywords_by_dim']，节省 2 次 LLM 调用。
"""

from pathlib import Path

import jieba
import jieba.analyse
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.safe_json_parser import SafeJsonOutputParser
from app.core.timing import timed_node
from app.prompt.prompt_loader import load_prompt

# 业务自定义词典：防止 GMV / dim_product 等术语被错误切分（刀 16）
_USERDICT_PATH = Path(__file__).parents[3] / "conf" / "jieba_userdict.txt"
if _USERDICT_PATH.exists():
    jieba.load_userdict(str(_USERDICT_PATH))


def _normalize_extended(raw) -> dict[str, list[str]]:
    """把 LLM 输出规范化为 {column/value/metric: list[str]}

    LLM 偶发输出非 dict / 字段不是 list / 元素非 str，统一兜底。
    """
    if not isinstance(raw, dict):
        return {"column": [], "value": [], "metric": []}
    result: dict[str, list[str]] = {}
    for key in ("column", "value", "metric"):
        v = raw.get(key, [])
        if not isinstance(v, list):
            v = []
        result[key] = [str(item) for item in v if item is not None]
    return result


async def _extend_keywords_unified(query: str) -> dict[str, list[str]]:
    """一次 LLM 调用产出三维度扩展词（column / value / metric）

    LLM 失败时降级为全空 dict，下游 recall 节点仍能基于 jieba 关键词检索。
    """
    prompt = PromptTemplate(
        template=load_prompt("extend_keywords_unified"),
        template_format="jinja2",
        input_variables=["query"],
    )
    chain = prompt | get_llm("extract_keywords") | SafeJsonOutputParser()
    try:
        result = await chain.ainvoke({"query": query})
        return _normalize_extended(result)
    except Exception as exc:
        logger.warning(f"[extend_keywords_unified] 扩展失败，降级为空: {exc}")
        return {"column": [], "value": [], "metric": []}


@timed_node
async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    step = "抽取关键词"
    writer = runtime.stream_writer
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # state['query'] 永远是用户原句（2026-07-14 改造后）
        query = state["query"]

        # 只保留更可能承载业务含义的词性，减少"的、帮我、一下"这类无检索价值的噪声
        allow_pos = (
            "n",  # 名词: 商品、订单、销售额
            "nr",  # 人名: 张三、李四
            "ns",  # 地名: 华北、北京、上海
            "nt",  # 机构团体名: 门店、品牌、渠道
            "nz",  # 其他专有名词: SKU、GMV、AOV
            "m",  # 数词: 3月、第一季度、前5个（刀 16 新增，避免时间/数量词被丢弃）
            "mq",  # 数量词: 万元、件、台（刀 16 新增）
            "v",  # 动词: 统计、对比、查询
            "vn",  # 名动词: 销售、成交、退款
            "a",  # 形容词: 新增、有效、活跃
            "an",  # 名形词: 可用、有效、异常
            "eng",  # 英文: GMV、SKU、ROI
            "i",  # 成语或习用语，避免遗漏整体表达
            "l",  # 常用固定短语，例如"销售总额"
        )

        # extract_tags 会基于 TF-IDF 抽取关键词，并按 allowPOS 做词性过滤
        keywords = jieba.analyse.extract_tags(query, allowPOS=allow_pos)

        # 2026-07-20 新增：一次 LLM 调用产出三维度扩展词（#16）
        # 替代原来三个 recall 节点各自调一次的分散模式，节省 2 次 LLM 调用
        extended_by_dim = await _extend_keywords_unified(query)

        writer({"type": "progress", "step": step, "status": "success"})
        logger.info(
            f"抽取关键词成功: keywords={keywords} extended_by_dim={extended_by_dim}"
        )
        return {
            "keywords": keywords,
            "extended_keywords_by_dim": extended_by_dim,
        }
    except Exception as e:
        logger.error(f"抽取关键词失败: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
