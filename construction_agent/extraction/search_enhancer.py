"""搜索增强模块：通过百度千帆联网搜索 API 补充信息不足的事故文本。

API: POST https://qianfan.baidubce.com/v2/ai_search/web_search
认证: Bearer QIANFAN_API_KEY (千帆 AppBuilder API Key)
触发: 文本 <200字 且 (无伤亡 或 无直接原因 或 事故类型不确定)

管道:
  1. 千帆搜索 → 收集同事故 snippets
  2. HTML 清洗 → 去标签
  3. 如果 snippets 总量 >800字 → LLM 总结提炼关键事实
  4. 注入原文 → 重跑 Step 1 提取
"""

from __future__ import annotations

import logging
import os
import re
import time as _time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

QIANFAN_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"

# snippets 合计超过此长度则触发 LLM 总结
SUMMARIZE_THRESHOLD = 800

SUMMARIZE_PROMPT = """你是一个事故信息提炼助手。以下是从多个来源搜索到的同一建筑施工事故的相关信息。请提炼出关键事实，以简洁的要点形式输出：

1. 事故发生时间、地点
2. 事故类型和直接原因
3. 伤亡人数（标注不同来源的差异，如有）
4. 涉及的施工单位/建设单位/监理单位（如有）
5. 其他值得注意的细节

只输出事实，不要推测。每个要点引用信息来源的编号。"""


class SearchEnhancer:
    """百度千帆搜索增强器。

    用法:
        enhancer = SearchEnhancer()
        if enhancer.needs_search(text, step1):
            result = enhancer.enhance(text, step1)
            # result["enriched_text"] → 重跑 Step 1
            # result["same_accident_snippets"] → 同事故多源信息
    """

    def __init__(self, api_key: Optional[str] = None, llm_client=None):
        self.api_key = api_key or os.getenv("QIANFAN_API_KEY") or ""
        self._enabled = bool(self.api_key)
        self._llm = llm_client  # 用于超长 snippet 总结
        if not self._enabled:
            logger.warning("QIANFAN_API_KEY 未配置，搜索增强不可用")

    # ═══════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════

    def needs_search(self, text: str, step1_result: Optional[dict] = None) -> bool:
        """判断是否需要搜索增强。"""
        score = 0
        if len(text) < 200:
            score += 3
        elif len(text) < 400:
            score += 1

        if step1_result:
            if not step1_result.get('deaths') and not step1_result.get('injuries'):
                score += 2
            dc = step1_result.get('direct_cause', '')
            if not dc or dc in ('未提及', '未知', None):
                score += 2
            if step1_result.get('accident_type') == '其他':
                score += 2
            if not step1_result.get('contractor') or step1_result.get('contractor') is None:
                score += 1

        return score >= 3

    def enhance(
        self, text: str, step1_result: Optional[dict] = None,
    ) -> dict:
        """千帆搜索增强，仅保留同事故的多源信息。

        Returns:
            {"enriched_text": 原文 + 同事故补充,
             "same_accident_snippets": [str],
             "same_accident_count": int,
             "total_results": int,
             "queries_used": [str],
             "search_performed": bool}
        """
        if not self._enabled:
            return {"enriched_text": text, "search_performed": False,
                    "queries_used": [], "same_accident_snippets": [],
                    "total_results": 0, "error": "API未配置"}

        queries = self._build_queries(text, step1_result)
        all_results: list[dict] = []

        for query in queries[:3]:
            try:
                results = self._search_qianfan(query, top=8)
                all_results.extend(results)
            except Exception as e:
                logger.warning(f"千帆搜索失败 [{query[:40]}]: {e}")

        if not all_results:
            return {"enriched_text": text, "search_performed": True,
                    "queries_used": queries, "same_accident_snippets": [],
                    "same_accident_count": 0, "total_results": 0,
                    "error": "搜索无结果"}

        # URL 去重
        seen = set()
        unique = []
        for r in all_results:
            key = (r.get("url", "")).rstrip('/')
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

        # 相关性过滤：仅保留同事故
        same, _ = self._classify_results(unique, step1_result)

        enriched_text = text
        if same:
            # HTML 清洗
            for s in same:
                s["snippet"] = self._strip_html(s.get("snippet", ""))
                s["title"] = self._strip_html(s.get("title", ""))

            # 构造多源信息
            lines = []
            for i, s in enumerate(same):
                lines.append(f"[来源{i+1}] {s['title']}")
                lines.append(f"{s['snippet']}")

            combined = "\n\n".join(lines)
            total_len = len(combined)

            # 超过阈值 → LLM 总结提炼
            if total_len > SUMMARIZE_THRESHOLD and self._llm:
                summary = self._summarize_snippets(same)
                if summary:
                    enriched_text = (
                        f"【同事故多源信息总结】\n{summary}\n\n"
                        f"【事故原文】\n{text}"
                    )
                    logger.info(f"LLM总结: {total_len}字 → {len(summary)}字")
                else:
                    enriched_text = f"【同事故多源信息】\n{combined}\n\n【事故原文】\n{text}"
            else:
                enriched_text = f"【同事故多源信息】\n{combined}\n\n【事故原文】\n{text}"

        return {
            "enriched_text": enriched_text,
            "search_performed": True,
            "queries_used": queries,
            "same_accident_snippets": [
                f"[{s['title']}] {s.get('content', s.get('snippet', ''))}"
                for s in same
            ],
            "same_accident_urls": [
                {"title": s["title"], "url": s["url"]} for s in same
            ],
            "same_accident_count": len(same),
            "total_results": len(unique),
            "error": None if same else "未找到同事故信息",
        }

    # ═══════════════════════════════════════════════
    # 千帆 API 调用
    # ═══════════════════════════════════════════════

    def _search_qianfan(self, query: str, top: int = 5) -> list[dict]:
        """调用千帆 web_search API。内置请求间隔避免 QPS 限流。"""
        import time as _time
        _time.sleep(0.6)  # 免费层 QPS≈1-2, 0.6s间隔确保不触发限流

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "messages": [{"content": query, "role": "user"}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": top}],
        }

        try:
            resp = httpx.post(QIANFAN_SEARCH_URL, headers=headers, json=data,
                            timeout=15.0)
            resp.raise_for_status()
            body = resp.json()

            results = []
            for ref in body.get("references", []):
                snippet = ref.get("content", "")
                title = ref.get("title", "")
                url = ref.get("url", "")
                if title and url:
                    results.append({
                        "title": title, "url": url, "snippet": snippet,
                    })
            return results

        except httpx.HTTPStatusError as e:
            logger.warning(f"千帆 HTTP {e.response.status_code} [{query[:40]}]")
        except Exception as e:
            logger.warning(f"千帆异常 [{query[:40]}]: {e}")

        return []

    # ═══════════════════════════════════════════════
    # 相关性过滤
    # ═══════════════════════════════════════════════

    def _classify_results(
        self, results: list[dict], step1: Optional[dict],
    ) -> tuple[list[dict], list[dict]]:
        """仅保留同事故结果。需城市+(日期|项目|类型|伤亡)双重匹配。

        优先级: 城市(硬门槛) → 日期 > 项目名 > 伤亡 > 类型 > 施工信号
        """
        if not step1:
            return results, []

        city = (step1.get('city') or '').replace('市', '').replace('县', '')
        acc_type = step1.get('accident_type', '')
        deaths = step1.get('deaths', 0) or 0
        project = (step1.get('project_name') or '')
        date_str = step1.get('date') or ''  # ISO: 2026-06-20

        same = []
        dropped = 0

        for r in results:
            combined = (r.get('title', '') + ' ' + r.get('snippet', ''))

            # ── 硬门槛: 城市必须匹配 ──
            if not city or len(city) < 2 or city not in combined:
                dropped += 1
                continue

            # ── 城市匹配后，累积额外证据 ──
            extra_score = 0

            # 日期匹配 (权重最高: 同月同日几乎一定是同一事故)
            if date_str and len(date_str) >= 10:
                date_score = self._match_date(date_str, combined)
                extra_score += date_score  # 月日匹配=+4, 仅月匹配=+2

            # 项目名
            if project and len(project) >= 4 and project[:6] in combined:
                extra_score += 3

            # 事故类型
            if acc_type and acc_type in combined:
                extra_score += 2

            # 伤亡
            if deaths > 0:
                ref_deaths = self._extract_deaths(combined)
                if ref_deaths > 0:
                    if ref_deaths == deaths: extra_score += 2
                    elif abs(ref_deaths - deaths) <= 1: extra_score += 1

            # 施工信号（弱证据）
            if any(kw in combined for kw in ['施工', '工地', '在建', '项目']):
                extra_score += 1

            # 日期匹配直接通过 (月日一致 = 强力证据)
            if date_score >= 4:
                same.append(r)
            elif extra_score >= 2:
                same.append(r)
            else:
                dropped += 1

        logger.debug(
            f"搜索过滤: {len(results)}条 → 同事故{len(same)}条, "
            f"丢弃{dropped}条"
        )
        return same, []

    @staticmethod
    def _match_date(iso_date: str, text: str) -> int:
        """在文本中查找与 ISO 日期匹配的日期模式。

        Args:
            iso_date: "2026-06-20"
            text: 搜索结果的标题+摘要

        Returns:
            score: 4=月日完全匹配, 2=仅月匹配, 0=未匹配
        """
        try:
            parts = iso_date.split('-')
            if len(parts) < 2:
                return 0
            year = parts[0]
            month = parts[1]
            day = parts[2] if len(parts) > 2 else ''
        except (ValueError, IndexError):
            return 0

        # 模式: "6月20日" "06月20日" "6.20" "2026-06-20"
        date_patterns = [
            f"{int(month)}月{int(day)}日",
            f"{month}月{day}日",
            f"{int(month)}.{int(day)}",
            f"{iso_date}",
            f"{year}年{int(month)}月{int(day)}日",
        ]

        for pat in date_patterns:
            if pat in text:
                return 4  # 月日完全匹配 → 极大概率同一事故

        # 仅月份匹配（弱一些）
        month_patterns = [
            f"{int(month)}月",
            f"{year}年{int(month)}月",
        ]
        for pat in month_patterns:
            if pat in text:
                return 2  # 仅月匹配 → 可能相关但不够确定

        return 0

    @staticmethod
    def _extract_deaths(text: str) -> int:
        """从文本提取死亡人数。"""
        for pat in [r'(\d+)人死亡', r'致(\d+)死', r'(\d+)人遇难',
                     r'造成(\d+)死', r'(\d+)死(\d+)伤']:
            m = re.search(pat, text)
            if m:
                return int(m.group(1))
        return 0

    @staticmethod
    def _strip_html(text: str) -> str:
        """去除 HTML 标签和常见转义字符。"""
        if not text: return ""
        text = re.sub(r'<[^>]+>', ' ', text)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _summarize_snippets(self, snippets: list[dict]) -> Optional[str]:
        """调用 LLM 对搜索到的多源信息进行总结提炼。"""
        if not self._llm:
            return None

        lines = []
        for i, s in enumerate(snippets):
            lines.append(f"=== 来源{i+1} ===")
            lines.append(f"标题: {s['title']}")
            lines.append(f"内容: {s['snippet']}")

        user_msg = "\n\n".join(lines)

        try:
            response = self._llm.messages.create(
                model="deepseek-v4-flash",
                max_tokens=1000,
                temperature=0.0,
                timeout=30,
                system=SUMMARIZE_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = ""
            for block in response.content:
                if hasattr(block, 'type') and block.type == 'thinking':
                    continue
                if hasattr(block, 'text'):
                    text += block.text
            return text.strip() or None
        except Exception as e:
            logger.warning(f"LLM 总结失败: {e}")
            return None

    # ═══════════════════════════════════════════════
    # 搜索词构造
    # ═══════════════════════════════════════════════

    def _build_queries(self, text: str, step1_result: Optional[dict]) -> list[str]:
        queries = []
        if step1_result:
            city = str(step1_result.get('city', '') or '')
            location = str(step1_result.get('location', '') or '')
            acc_type = str(step1_result.get('accident_type', '') or '')
            deaths = step1_result.get('deaths', 0) or 0
            project = str(step1_result.get('project_name', '') or '')
            title = str(step1_result.get('title', '') or '')

            # 精确查询
            if city and acc_type:
                q = f"{city} {acc_type}"
                if deaths: q += f" {deaths}人死亡"
                queries.append(q)
            # 项目名
            if project and len(project) >= 4 and project not in ('未提及', 'null'):
                queries.append(f"{project} 施工事故")
            # 标题（去尾）
            if len(title) >= 8:
                queries.append(title[:50])
            # 省级
            if location and location != city and len(queries) < 3:
                queries.append(f"{location} {acc_type}事故" if acc_type else f"{location} 建筑施工事故")

        if not queries:
            keyword = text[:80].replace('\n', ' ')
            if len(keyword) >= 6:
                queries.append(keyword)

        seen = set()
        unique = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                unique.append(q)
        return unique[:4]
