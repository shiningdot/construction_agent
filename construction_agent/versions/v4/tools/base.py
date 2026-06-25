"""V4 Tool 定义。"""

from typing import Any


class ToolSpec:
    """工具规范：描述 + JSON Schema。"""
    def __init__(self, name: str, description: str, schema: dict, fn: callable):
        self.name = name
        self.description = description
        self.schema = schema
        self.fn = fn

    def to_tool_def(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}

    def execute(self, **kwargs) -> Any:
        return self.fn(**kwargs)


# ---- 工具函数 ----

def tool_search(query: str, max_results: int = 5) -> str:
    from construction_agent.search.baidu_search import BaiduSearchEngine
    se = BaiduSearchEngine()
    results = se.search(query, count=max_results)
    if not results:
        return "未找到相关结果"
    lines = [f"找到 {len(results)} 条结果："]
    for i, r in enumerate(results[:max_results], 1):
        lines.append(f"{i}. {r.title}\n   {r.snippet[:100]}")
    return "\n".join(lines)


def tool_extract(text: str) -> dict:
    from construction_agent.extraction.claude_extractor import ClaudeExtractor
    ex = ClaudeExtractor()
    result = ex.extract(text)
    if result is None:
        return {"error": "提取失败"}
    accident = result.to_accident(raw_input=text)
    d = accident.to_dict()
    return {
        "title": d.get("title",""),
        "accident_type": d.get("accident_type",""),
        "severity": d.get("severity",""),
        "deaths": d.get("deaths",0),
        "injuries": d.get("injuries",0),
        "location": d.get("location",""),
        "city": d.get("city",""),
        "direct_cause": d.get("direct_cause",""),
        "contractor": d.get("contractor",""),
        "human_factors": len(d.get("human_factors",[]) or []),
        "machine_factors": len(d.get("machine_factors",[]) or []),
        "management_factors": len(d.get("management_factors",[]) or []),
        "environmental_factors": len(d.get("environmental_factors",[]) or []),
        "summary": d.get("summary","")[:100],
    }


def tool_classify(cause_text: str) -> str:
    from construction_agent.hfacs import HfacsTaxonomy
    tax = HfacsTaxonomy()
    tax.load()
    results = tax.classify(cause_text, "")
    if not results:
        return "无法分类"
    codes = [f"{code}({cname})" for code, lname, cname in results]
    return "致因分类: " + ", ".join(codes)


# ---- 注册工具 ----

SEARCH_TOOL = ToolSpec(
    "search", "搜索互联网获取最新信息",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索关键词"},
        "max_results": {"type": "integer", "description": "返回结果数"},
    }, "required": ["query"]},
    tool_search,
)

EXTRACT_TOOL = ToolSpec(
    "extract", "从事故文本中提取结构化数据",
    {"type": "object", "properties": {
        "text": {"type": "string", "description": "事故描述文本"},
    }, "required": ["text"]},
    tool_extract,
)

CLASSIFY_TOOL = ToolSpec(
    "classify_cause", "对事故致因进行HFACS-CS分类",
    {"type": "object", "properties": {
        "cause_text": {"type": "string", "description": "致因描述文本"},
    }, "required": ["cause_text"]},
    tool_classify,
)

ALL_TOOLS = [SEARCH_TOOL, EXTRACT_TOOL, CLASSIFY_TOOL]
