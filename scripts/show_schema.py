"""显示 Accident 模型完整 schema"""
from construction_agent.models.accident import Accident

fields = Accident.model_fields

print("## Accident 数据模型 (Pydantic v2) — 38 字段\n")

groups = {
    "唯一标识": ["id"],
    "基础信息": ["title","date","accident_time","location","city","accident_location_detail",
                 "project_name","project_type","building_structure","building_completion_year",
                 "construction_phase","weather"],
    "事故属性": ["accident_type","severity"],
    "伤亡经济": ["deaths","injuries","trapped_count","direct_economic_loss"],
    "致因(人)": ["human_factors"],
    "致因(机)": ["machine_factors"],
    "致因(管)": ["management_factors"],
    "致因(环)": ["environmental_factors"],
    "时间派生": ["day_of_week","season","time_period","lighting"],
    "HFACS": ["hfacs_codes","hfacs_details","hfacs_encoding_version"],
    "来源标注": ["source","summary","direct_cause","contractor","source_urls"],
    "搜索": ["search_snippets","search_queries"],
    "图片": ["images","image_urls"],
    "元数据": ["raw_input","created_at"],
}

for group, keys in groups.items():
    print(f"### {group}")
    for k in keys:
        if k not in fields:
            continue
        f = fields[k]
        # 类型
        t = str(f.annotation)
        t = t.replace("typing.Optional[", "").replace("typing.", "")
        t = t.replace("datetime.", "")
        t = t.replace("list[HfacsCode]", "list[dict]")
        if "list[" in t:
            t_simple = t.replace("construction_agent.models.accident.", "")
            t = t_simple
        t = t.replace("construction_agent.models.accident.", "")
        t = t.rstrip("]").replace("Optional[", "")

        # 默认值
        d = str(f.default)
        d = d.replace("PydanticUndefined", "必填").replace("default_factory", "")
        # 描述
        desc = f.description or ""

        print(f"| `{k}` | `{t}` | {d} | {desc} |")
    print()

# 存储方式
print("---")
print("\n## 存储方式\n")
print("| 层级 | 方式 | 路径 |")
print("|------|------|------|")
print("| 主存储 | JSON 文件 | `data/accidents_merged.json` |")
print("| 加载 | DatasetManager 内存载入 | `__init__ → _load() → json.loads` |")
print("| 写入 | 每次 add() 后 _save() | `json.dumps(data, indent=2, ensure_ascii=False)` |")
print("| 模型 | Pydantic BaseModel | `.model_dump()` 序列化 / `**dict` 反序列化 |")
print("| 导出 | JSONFormatter/CSVFormatter | `to_flat_dict()` → 扁平化 → CSV |")
print("| 查重 | 基于 MD5(title+date+city) | `id` 字段自动生成 |")
