"""采集流水线编排器。

负责编排整个数据采集流程：
1. 激活各数据源采集器 → 获取原始文本
2. 文本清洗（TextCleaner）
3. 规则字段提取（RuleBasedFieldExtractor）
4. 标准化（Normalizer）
5. 四层查重（Deduplicator）→ 过滤重复
6. 对于需要 LLM 增强的记录 → 调用 ExtractionPipeline
7. 入库（DatasetManager）
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.collector.cleaners.text_cleaner import TextCleaner, get_cleaner
from construction_agent.collector.cleaners.field_extractor import (
    RuleBasedFieldExtractor,
    get_extractor,
)
from construction_agent.collector.cleaners.normalizer import Normalizer, get_normalizer
from construction_agent.collector.cleaners.deduplicator import Deduplicator
from construction_agent.config import settings
from construction_agent.database import DatasetManager

logger = logging.getLogger(__name__)


class CollectorPipeline:
    """数据采集流水线。

    用法：
        pipeline = CollectorPipeline()
        pipeline.register_collector(SafehooCollector())
        pipeline.register_collector(AcademicDatasetImporter())
        stats = pipeline.run()
        print(f"新增 {stats['added']} 条事故记录")
    """

    def __init__(
        self,
        enable_llm: bool = False,  # 默认不启用 LLM（省钱模式）
        dataset_path: Optional[Path] = None,
    ):
        """
        Args:
            enable_llm: 是否启用 LLM 增强提取（默认 False，仅规则提取）
            dataset_path: 数据集 JSON 路径
        """
        self.enable_llm = enable_llm
        self.collectors: list[BaseCollector] = []

        # 清洗层组件
        self.cleaner = get_cleaner()
        self.field_extractor = get_extractor()
        self.normalizer = get_normalizer()
        self.deduplicator = Deduplicator()

        # 存储层
        self.dataset = DatasetManager(filepath=dataset_path)

        # LLM 提取器（延迟加载）
        self._extraction_pipeline = None

        # 统计
        self.stats = {
            "total_collected": 0,
            "total_after_clean": 0,
            "duplicates_filtered": 0,
            "llm_enhanced": 0,
            "added": 0,
            "skipped": 0,
            "merged": 0,
            "errors": 0,
            "by_source": {},
            "by_layer": {"L1": 0, "L2": 0, "L3": 0, "L3_fallback": 0, "L4": 0},
        }

        # ── 加载已有数据集索引 ──
        self._load_existing_index()

    def register_collector(self, collector: BaseCollector):
        """注册一个数据源采集器。

        Args:
            collector: BaseCollector 实例
        """
        self.collectors.append(collector)
        logger.info(f"已注册采集器: {collector.source_name()}")

    def _load_existing_index(self):
        """加载已有数据集以构建去重索引。"""
        try:
            existing_records = self.dataset.get_all()
            if existing_records:
                self.deduplicator.load_existing_dataset(existing_records)
                logger.info(f"已加载 {len(existing_records)} 条现有记录到去重索引")
        except Exception as e:
            logger.warning(f"加载已有数据集失败: {e}")

    def _get_llm_pipeline(self):
        """延迟加载 LLM 提取管道。"""
        if self._extraction_pipeline is None:
            try:
                from construction_agent.extraction.claude_extractor import ExtractionPipeline
                self._extraction_pipeline = ExtractionPipeline()
            except Exception as e:
                logger.warning(f"LLM 提取管道加载失败: {e}")
                self._extraction_pipeline = False
        return self._extraction_pipeline if self._extraction_pipeline is not False else None

    def run(self) -> dict:
        """运行完整采集流水线。

        Returns:
            统计信息 dict
        """
        t_start = time.time()

        for collector in self.collectors:
            source_name = collector.source_name()
            logger.info(f"{'='*50}")
            logger.info(f"开始采集: {source_name}")
            logger.info(f"{'='*50}")

            try:
                # Step 1: 采集原始数据
                raw_items = collector.collect()

                # **先判形态，再碰 `len()`。** `collect()` 有两种返回形态
                # （详见 `collector/executor.py` 模块 docstring）：5 个源返回
                # `list[RawAccidentText]`，9 个 store-backed 的源返回
                # `_make_stats()` 统计字典。
                #
                # 字典这一路会**静默出错**，而且错得很有欺骗性——
                # `len(dict)` **不报错**，给的是键的个数（实测 8），于是
                # 「一条文本都没有」被显示成「采集 8 条」；紧接着
                # `for item in dict` 遍历出的是**键名字符串**，
                # `item.raw_text` 抛 `AttributeError`，被下面的
                # `except Exception` 吞成一条 `logger.debug`——默认 INFO 级别
                # 下**一个字都看不见**。实测（2026-10-01）整段日志是：
                #
                #     Step 1 采集: 8 条原始文本
                #     Step 2 清洗: 0 条有效文本（过滤 8 条）
                #
                # **看起来像清洗环节把 8 条全滤掉了**——去查清洗规则、去调
                # 最短长度阈值，永远查不到真因。
                if not isinstance(raw_items, list):
                    logger.warning(
                        f"  Step 1 采集: {source_name} 的 collect() 返回的是 "
                        f"{type(raw_items).__name__} 统计字典，不是文本列表。"
                        f"本源的产出走 raw_store 通路，旧编排器这一路没有东西"
                        f"可处理，跳过（**条数不是 {len(raw_items)}**）"
                    )
                    self.stats["by_source"][source_name] = {
                        "collected": 0, "added": 0, "duplicates": 0, "merged": 0,
                        "note": "collect() 返回统计字典，产出已走 raw_store 通路",
                    }
                    continue

                self.stats["total_collected"] += len(raw_items)
                self.stats["by_source"][source_name] = {
                    "collected": len(raw_items),
                    "added": 0,
                    "duplicates": 0,
                    "merged": 0,
                }
                logger.info(f"  Step 1 采集: {len(raw_items)} 条原始文本")

                # Step 2: 文本清洗
                cleaned_items = []
                for item in raw_items:
                    try:
                        item.raw_text = self.cleaner.clean(
                            item.raw_text or item.raw_html,
                            is_html=bool(item.raw_html),
                        )
                        if item.raw_text and len(item.raw_text) >= 30:
                            cleaned_items.append(item)
                    except Exception as e:
                        logger.debug(f"清洗失败: {e}")
                        continue

                self.stats["total_after_clean"] += len(cleaned_items)
                logger.info(f"  Step 2 清洗: {len(cleaned_items)} 条有效文本（过滤 {len(raw_items) - len(cleaned_items)} 条）")

                # Step 3: 规则字段提取
                for item in cleaned_items:
                    try:
                        self.field_extractor.extract(item)
                    except Exception as e:
                        logger.debug(f"字段提取失败: {e}")
                        continue

                logger.info(f"  Step 3 规则提取: 完成 {len(cleaned_items)} 条")

                # Step 4: 去重
                new_items = []
                for item in cleaned_items:
                    try:
                        result = self.deduplicator.check(item)
                        if result["is_duplicate"]:
                            # 合并信息到已有记录
                            self.deduplicator.merge(item, result["matched_id"])
                            self.stats["duplicates_filtered"] += 1
                            self.stats["by_source"][source_name]["duplicates"] += 1
                            # 更新已有数据集记录
                            if result["matched_id"] in self.deduplicator._records:
                                merged_record = self.deduplicator._records[result["matched_id"]]
                                # 写回 DatasetManager
                                from construction_agent.models.accident import Accident
                                try:
                                    accident = Accident(**merged_record)
                                    self.dataset.add(accident)
                                    self.stats["merged"] += 1
                                    self.stats["by_source"][source_name]["merged"] += 1
                                except Exception:
                                    pass

                            layer = result.get("layer", "L2")
                            self.stats["by_layer"][layer] = self.stats["by_layer"].get(layer, 0) + 1
                        else:
                            new_items.append(item)
                    except Exception as e:
                        logger.debug(f"去重检查失败: {e}")
                        new_items.append(item)  # 无法判断时不丢弃

                logger.info(
                    f"  Step 4 去重: {len(new_items)} 条新记录 "
                    f"(过滤 {len(cleaned_items) - len(new_items)} 条重复)"
                )

                # Step 5: LLM 增强（可选）
                if self.enable_llm:
                    llm_pipeline = self._get_llm_pipeline()
                    if llm_pipeline:
                        enhanced_count = 0
                        for item in new_items:
                            if item.needs_llm_enhancement():
                                try:
                                    accident = llm_pipeline.process(
                                        item.raw_text,
                                        enable_search=False,  # 采集时已有点击内容，不重复搜索
                                        source=item.source,
                                    )
                                    if accident:
                                        item.extra["llm_enhanced"] = True
                                        item.extra["accident_obj"] = accident
                                        enhanced_count += 1
                                except Exception as e:
                                    logger.debug(f"LLM 增强失败: {e}")
                                    continue

                        self.stats["llm_enhanced"] += enhanced_count
                        logger.info(f"  Step 5 LLM增强: {enhanced_count} 条")
                else:
                    logger.info(f"  Step 5 LLM增强: 已禁用（省钱模式）")

                # Step 6: 转换为 Accident 并入厍
                added_count = 0
                for item in new_items:
                    try:
                        accident = self._raw_to_accident(item)
                        if accident:
                            result = self.dataset.add(accident)
                            if result["action"] in ("added", "updated"):
                                added_count += 1

                            # 同步更新去重器索引
                            self.deduplicator.add(item, accident.id)
                    except Exception as e:
                        logger.debug(f"入库失败: {e}")
                        self.stats["errors"] += 1
                        continue

                self.stats["added"] += added_count
                self.stats["by_source"][source_name]["added"] = added_count
                logger.info(f"  Step 6 入库: {added_count} 条新增")

            except Exception as e:
                logger.error(f"采集器 {source_name} 运行异常: {e}", exc_info=True)
                self.stats["errors"] += 1
                continue

        self.stats["duration_seconds"] = round(time.time() - t_start, 1)
        logger.info(f"\n{'='*50}")
        logger.info(f"采集完成! 耗时 {self.stats['duration_seconds']}s")
        logger.info(f"  采集总数: {self.stats['total_collected']}")
        logger.info(f"  清洗后: {self.stats['total_after_clean']}")
        logger.info(f"  去重过滤: {self.stats['duplicates_filtered']}")
        logger.info(f"  新增入库: {self.stats['added']}")
        logger.info(f"  合并已有: {self.stats['merged']}")
        logger.info(f"  LLM增强: {self.stats['llm_enhanced']}")
        logger.info(f"  数据集总量: {self.dataset.stats()['total']}")
        logger.info(f"{'='*50}")

        return self.stats

    def _raw_to_accident(self, item: RawAccidentText):
        """将 RawAccidentText 转换为 Accident 模型。

        优先使用 LLM 预创建的对象，否则基于规则提取字段构建基础 Accident。
        """
        # 如果已经 LLM 增强过，直接使用
        if item.extra.get("accident_obj"):
            return item.extra["accident_obj"]

        # 基于规则提取的字段构建 Accident
        from construction_agent.models.accident import Accident

        # 地图：规则提取的事故类型 → 标准值
        # Normalizer 已经做了标准化，这里做二次确认
        accident_type = item.extracted_accident_type or "其他"
        valid_types = {"坍塌", "高处坠落", "触电", "机械伤害", "起重伤害",
                       "物体打击", "土方坍塌", "火灾爆炸", "中毒窒息",
                       "淹溺", "车辆伤害", "其他"}
        if accident_type not in valid_types:
            # 再次尝试标准化
            atype, _ = self.normalizer.normalize_accident_type(
                item.title + "\n" + item.raw_text[:1000]
            )
            if atype and atype in valid_types:
                accident_type = atype

        # 事故等级
        severity = item.extracted_severity or "一般事故"

        # 经济损失
        loss = item.extra.get("direct_economic_loss")

        accident = Accident(
            title=item.title or "未知事故",
            date=item.extracted_date,
            location=item.extracted_province or "",
            city=item.extracted_city or "",
            accident_type=accident_type,
            severity=severity,
            deaths=item.extracted_deaths,
            injuries=item.extracted_injuries,
            direct_economic_loss=loss,
            direct_cause=item.extra.get("contractor", ""),
            contractor=item.extra.get("contractor", ""),
            project_name=item.extra.get("project_name", ""),
            summary=item.raw_text[:500] if item.raw_text else "",
            source=item.source,
            source_urls=[item.source_url] if item.source_url else [],
            raw_input=item.raw_text or "",
        )

        # 自动推导派生字段
        accident.auto_derive_fields()

        # 将 trapped_count 等信息存入 extra（TODO: 扩展 Accident 模型）
        if item.extra.get("trapped_count"):
            accident.trapped_count = item.extra["trapped_count"]

        return accident

    def save_stats(self, output_path: Optional[Path] = None):
        """保存采集统计报告。"""
        path = output_path or (settings.DATA_DIR / "collect_stats.json")
        path.parent.mkdir(parents=True, exist_ok=True)

        # 补充去重器统计
        self.stats["deduplicator"] = self.deduplicator.stats()

        path.write_text(
            json.dumps(self.stats, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"统计报告已保存: {path}")
