"""调试 DeepSeek 响应格式，排查 JSON 解析失败原因。"""
import os, json, re, logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(message)s")

from anthropic import Anthropic

api_key = os.getenv("ANTHROPIC_API_KEY")
base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

client = Anthropic(api_key=api_key, base_url=base_url)

cases = json.loads(Path("data/book_cases.json").read_text(encoding="utf-8"))

# 测试失败案例1: 凤凰堤溪沱江大桥
for case_idx in [0, 21]:
    case = cases[case_idx]
    print(f'{"="*60}')
    print(f'测试案例 {case_idx+1}: {case["title"][:80]}')
    print(f'文本长度: {len(case["text"])} 字符')

    prompt = (
        "请将以下建筑安全事故信息提取为结构化JSON。"
        "只输出一个JSON对象，包含以下字段：title, date, location, city, "
        "accident_type, severity, deaths, injuries, direct_economic_loss, "
        "weather, direct_cause, contractor, summary, "
        "human_factors(数组,每项含category和description), "
        "machine_factors(数组,每项含category和description), "
        "management_factors(数组,每项含category和description), "
        "environmental_factors(数组,每项含category和description)。"
        "不要用markdown代码块，直接输出纯JSON。\n\n"
        + case["text"]
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system="你是建筑施工安全专家。请从事故描述中提取关键信息，以纯JSON格式返回，不要添加markdown格式。",
        messages=[{"role": "user", "content": prompt}],
    )

    print(f"stop_reason: {response.stop_reason}")
    print(f"content blocks: {len(response.content)}")
    raw = ""
    for blk in response.content:
        print(f"  block.type={blk.type}", end="")
        if hasattr(blk, "text"):
            raw = blk.text
            print(f" text_len={len(raw)}")
        elif hasattr(blk, "thinking"):
            print(f" thinking_len={len(blk.thinking)}")
        elif hasattr(blk, "name"):
            print(f" name={blk.name}")
        else:
            print()
    if not raw:
        print("❌ 没有找到 text block!")
        continue
    print(f"响应长度: {len(raw)} 字符")
    print(f"响应前200字符:\n{raw[:200]}")

    # 尝试解析
    parsed = False
    # 尝试1: ```json
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        try:
            data = json.loads(m.group(1))
            print(f"✅ ```json 解析成功: title={data.get('title','?')}")
            parsed = True
        except Exception as e:
            print(f"  ```json 解析失败: {e}")

    # 尝试2: 直接 {...}
    if not parsed:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                data = json.loads(m.group(0))
                print(f"✅ {{}} 解析成功: title={data.get('title','?')}")
                parsed = True
            except Exception as e:
                print(f"  {{}} 解析失败: {e}")
                print(f"  匹配文本前300: {m.group(0)[:300]}")

    if not parsed:
        print(f"❌ 无法解析!")
        # 保存原始响应用于分析
        dump = {"raw_response": raw, "case_idx": case_idx + 1}
        Path(f"/tmp/debug_case{case_idx+1}.json").write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"原始响应已保存到 /tmp/debug_case{case_idx+1}.json")

    print()

print("调试完成")
