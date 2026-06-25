"""V4 Agent Core: ReAct 循环。"""

import json, logging, time
from anthropic import Anthropic
from construction_agent.config import settings
from construction_agent.versions.v4.tools.base import ALL_TOOLS

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """你是一名建筑施工事故分析 Agent。
你的目标是根据用户提供的事故信息提取结构化数据。

流程：
1. 收到文本后，先判断信息是否充足
2. 如果信息不足（如缺少地点、伤亡等关键信息），使用 search 工具搜索补充
3. 信息充足后，使用 extract 工具提取结构化数据
4. 如果有致因信息，使用 classify_cause 进行分类

注意：一次只调用一个工具，等待结果后再决定下一步。"""


class AgentCore:
    def __init__(self):
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None

    def run(self, text: str, max_steps: int = 5) -> dict:
        """运行 Agent ReAct 循环处理一条事故文本。"""
        if not self.client:
            return {"error": "API Key 未配置"}

        messages = [{"role": "user", "content": f"请分析以下建筑施工事故：\n\n{text}"}]
        tool_defs = [t.to_tool_def() for t in ALL_TOOLS]
        
        result = {"steps": [], "final_output": None}
        
        for step in range(max_steps):
            t0 = time.time()
            response = self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=2000,
                system=AGENT_SYSTEM_PROMPT,
                tools=tool_defs,
                messages=messages,
            )
            elapsed = time.time() - t0
            
            # 记录 thinking
            thinking = ""
            for block in response.content:
                if block.type == "thinking":
                    thinking = block.thinking[:200]
            
            # 解析响应
            tool_calls = [b for b in response.content if b.type == "tool_use"]
            text_responses = [b.text for b in response.content if b.type == "text"]
            
            if not tool_calls:
                # Agent 完成，返回文本
                result["final_output"] = text_responses[0] if text_responses else "已完成"
                result["steps"].append({"step": step, "action": "complete", "thinking": thinking, "time": round(elapsed,1)})
                break
            
            for tc in tool_calls:
                fn_name = tc.name
                fn_args = tc.input
                
                # 找到并执行工具
                matched = [t for t in ALL_TOOLS if t.name == fn_name]
                if matched:
                    try:
                        tool_result = matched[0].execute(**fn_args)
                    except Exception as e:
                        tool_result = f"工具执行失败: {e}"
                else:
                    tool_result = f"未知工具: {fn_name}"
                
                result["steps"].append({
                    "step": step, "action": fn_name, "args": fn_args,
                    "thinking": thinking, "time": round(elapsed,1),
                    "result": str(tool_result)[:200],
                })
                
                # 把工具结果放回对话
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(tool_result, ensure_ascii=False)}],
                })
        
        return result

    def extract_structured(self, text: str) -> dict:
        """简化版：直接提取（不经过搜索→判断的ReAct循环，用于批量评测）。"""
        from construction_agent.versions.v2.pipeline import V2Pipeline
        p = V2Pipeline()
        r = p.process_single(text)
        if r.success and r.accident:
            return {"success": True, "accident": r.accident.to_dict()}
        return {"success": False, "error": r.error_message}
