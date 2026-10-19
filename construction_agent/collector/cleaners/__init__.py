"""数据清洗管道。

职责：对采集的原始文本进行多层处理，全部使用纯代码（不调用 LLM）：
- text_cleaner.py: HTML 去除、空白规范化、编码修复
- normalizer.py: 日期/地点/事故类型标准化
- field_extractor.py: 基于正则和规则的字段提取（核心省钱模块）
- deduplicator.py: 四层递进式查重
"""
