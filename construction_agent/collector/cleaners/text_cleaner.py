"""文本清洗器：将网页HTML或原始文本清洗为干净纯文本。

所有处理均为纯代码（正则/字符串操作），不调用 LLM。
"""

from __future__ import annotations

import html as html_module
import re
import unicodedata


class TextCleaner:
    """原始文本 → 干净纯文本的清洗管道。"""

    # 常见页面噪声模式（正则）
    # 中文标点下的导航/页脚/广告模式
    NOISE_PATTERNS: list[str] = [
        r'版权所有[©Copyright\s]*.*?(?:公司|单位|中心|局)',
        r'[©ⓒ]\s*\d{4}.*?(?:All Rights Reserved|版权所有)',
        r'技术支持[：:].*?(?:公司|科技|网络|信息)',
        r'主办单位[：:].*',
        r'(?:建议|推荐).*?浏览[器览].*?分辨率.*',
        r'ICP[证备].{3,20}号',
        r'网站标识码[：:].*',
        r'版权声明.*',
        r'免责声明[：:].*',
        r'隐私政策[：:].*',
        # 残留的 HTML 注释
        r'<!--[\s\S]*?-->',
    ]

    # 需要去除的空字符
    ZERO_WIDTH_CHARS = {
        '​',  # 零宽空格
        '‌',  # 零宽非连接符
        '‍',  # 零宽连接符
        '‎',  # 左至右标记
        '‏',  # 右至左标记
        '﻿',  # BOM
        '­',  # 软连字符
        '⁠',  # 词连接符
    }

    def __init__(self, max_text_length: int = 50000):
        """初始化清洗器。

        Args:
            max_text_length: 最大保留文本长度（字符），超过则截断
        """
        self.max_text_length = max_text_length
        self.noise_re = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in self.NOISE_PATTERNS]

    def clean(self, text: str, is_html: bool = False) -> str:
        """清洗文本。

        Args:
            text: 原始文本或 HTML
            is_html: 是否为 HTML 格式

        Returns:
            清洗后的纯文本
        """
        if not text or not text.strip():
            return ""

        # Step 1: HTML 清理
        if is_html or self._looks_like_html(text):
            text = self._clean_html(text)

        # Step 2: 基础清洗
        text = self._basic_clean(text)

        # Step 3: 去除噪声行
        text = self._remove_noise_lines(text)

        # Step 4: 截断过长文本
        if len(text) > self.max_text_length:
            # 保留前半部分（通常正文在前面）
            text = text[:self.max_text_length]

        return text.strip()

    def _looks_like_html(self, text: str) -> bool:
        """启发式判断文本是否为 HTML。"""
        html_markers = ['<html', '<head', '<body', '<div', '<p>', '<br', '<a ', '<table',
                        '<!DOCTYPE', '<meta', '<script', '<style']
        text_lower = text[:2000].lower()
        return any(marker in text_lower for marker in html_markers)

    def _clean_html(self, html_text: str) -> str:
        """从 HTML 中提取纯文本。

        渐进式：优先使用 trafilatura（如果可用），否则回退到 BeautifulSoup。
        """
        # 优先使用 trafilatura（专业正文提取）
        try:
            import trafilatura
            extracted = trafilatura.extract(
                html_text,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_precision=True,
                output_format='txt',  # 使用 txt 格式而非 markdown
            )
            if extracted and len(extracted.strip()) > 50:
                return extracted
        except ImportError:
            pass
        except Exception:
            pass

        # 回退: BeautifulSoup
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, "lxml")

            # 移除不需要的标签
            for tag_name in ["script", "style", "nav", "footer", "iframe", "noscript",
                             "form", "select", "button", "input", "textarea"]:
                for tag in soup.find_all(tag_name):
                    tag.decompose()

            # 提取文本
            text = soup.get_text(separator="\n")
        except ImportError:
            # 最后的回退：正则去标签
            text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_text, flags=re.IGNORECASE)
            text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = html_module.unescape(text)
        except Exception:
            text = html_text

        return text

    def _basic_clean(self, text: str) -> str:
        """基础文本清洗。"""
        # HTML 实体解码
        text = html_module.unescape(text)

        # 去除零宽字符
        for ch in self.ZERO_WIDTH_CHARS:
            text = text.replace(ch, '')

        # Unicode 规范化
        text = unicodedata.normalize('NFKC', text)

        # URL 编码字符解码
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'")

        # 统一换行符
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # 压缩连续空白（保留换行）
        text = re.sub(r'[ \t]+', ' ', text)

        # 压缩连续换行（最多保留2个）
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)

        # 去除行首行尾空白
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)

        return text

    def _remove_noise_lines(self, text: str) -> str:
        """去除噪声行（页脚、版权、导航等）。"""
        lines = text.split('\n')
        kept = []

        for line in lines:
            if not line.strip():
                kept.append(line)
                continue

            # 检查是否匹配噪声模式
            is_noise = False
            for pattern in self.noise_re:
                if pattern.search(line):
                    is_noise = True
                    break

            if is_noise:
                continue

            # 过短的行（<4个中文字符）通常是无意义内容
            # 但保留纯数字行（可能是伤亡数据）
            stripped = line.strip()
            if len(stripped) < 4 and not re.search(r'\d', stripped):
                continue

            kept.append(line)

        return '\n'.join(kept)


# 模块级单例
_default_cleaner: TextCleaner | None = None


def get_cleaner() -> TextCleaner:
    """获取默认清洗器单例。"""
    global _default_cleaner
    if _default_cleaner is None:
        _default_cleaner = TextCleaner()
    return _default_cleaner
