# 测试 fixture 接管所有 LLM 调用 - 2026-07-14

## 1. 背景

rebase 到远程 main 后，链路多 4 个 LLM 节点（_extract_inherited_context +
3 个 extend_keywords_with_llm + generate_intent）。这些节点模块级 import
`llm`，**单测 fixture 没接管**，每个节点都连远端 M3 失败重试。

## 2. 现象

跑测试从 7 秒 → 33-90 秒，因为：
- 每个 LLM 节点都连远端失败（测试时无 M3 凭据）
- 失败后 parse LLM 输出又调一次 M3
- 重试 + 超时累计

日志关键证据：
```
WARNING  ... extend_keywords_for_column_recall 扩展失败，使用空列表
WARNING  ... extend_keywords_for_metric_recall 扩展失败，使用空列表
WARNING  ... extend_keywords_for_value_recall 扩展失败，使用空列表
ERROR    ... generate_intent 生成查询意图 failed
```

## 3. 修复

### 3.1 fixture 注入所有 LLM 节点

`tests/test_sql_stability_smoke.py`：

```python
def _install_fake_llm(fake):
    """把桩 LLM 注入到所有用到 llm 的节点模块。"""
    llm_mod.llm = fake
    import app.agent.nodes.classify_intent as m_ci
    import app.agent.nodes.rewrite_query as m_rw
    import app.agent.nodes.filter_table as m_ft
    import app.agent.nodes.filter_metric as m_fm
    import app.agent.nodes.generate_intent as m_gi  # 远程 d9af4603
    import app.agent.nodes.generate_sql as m_gs
    import app.agent.nodes.correct_sql as m_cs
    import app.agent.nodes.respond_chitchat as m_ch
    import app.agent.nodes._recall_helpers as m_rh  # extend_keywords_with_llm
    for m in (m_ci, m_rw, m_ft, m_fm, m_gi, m_gs, m_cs, m_ch, m_rh):
        m.llm = fake
```

### 3.2 修远程 prompt 的 f-string 嵌套 bug

`prompts/generate_intent.prompt` / `prompts/extract_inherited_context.prompt`：
示例 JSON 里的 `{...}` 必须转义成 `{{...}}`，否则 LangChain PromptTemplate
渲染报"Invalid format specifier"。

### 3.3 filter_table / filter_metric 降级

让坏 JSON 不中断链路，对齐 _recall_helpers 的降级行为：

```python
except Exception as e:
    logger.warning(f"{step} failed: {e}, 降级保留全部候选表")
    return {"table_infos": table_infos}  # 不再 raise
```

### 3.4 replies 序列对齐 LLM 调用顺序

rebase 后链路实际 LLM 调用顺序：

```
0: classify_intent
1: rewrite._extract_inherited_context
2: rewrite 主体
3-5: extend_keywords × 3
6: filter_table
7: filter_metric
8: generate_intent
9: generate_sql（fallback 链）
10-11: correct_sql × 2
```

replies 必须按这个顺序准备，长度足够。

## 4. 验证

| 测试 | 之前 | 之后 |
|---|---|---|
| `test_sql_stability_smoke.py` (4) | 33-90s, 部分失败 | **4/4 PASSED** |
| `test_deterministic_resolver_smoke.py` (13) | 0.52s | 13/13 PASSED |
| **总计** | **40-100s, 14/17 PASSED** | **17/17 PASSED, 7.26s** |

## 5. 经验沉淀

1. **fixture 接管必须穷举**：rebase 引入新 LLM 节点时，fixture 必须跟着加，
   否则每次都要手动维护 reply 序列长度。
2. **prompt 转义硬规则**：LangChain PromptTemplate 把 `{` `}` 当变量占位符，
   示例 JSON 必须用 `{{` `}}` 转义。
3. **链路稳定性节点必须降级**：filter_table / filter_metric / extend_keywords
   任何一个抛异常都会让整张图崩，对齐"单节点异常不阻塞整体流程"原则。
4. **replies 数量 = LLM 调用次数**：fixture 按调用顺序发 reply，准备足够长度。

## 6. 推送到 GitHub

commit `5884127` 已 push 到 origin/main。