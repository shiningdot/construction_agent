"""MinerU PDF 事故文档处理器。

支持 flash 模式（免认证，≤10MB/20页）和 precision 模式（需 Token，≤200MB/600页）。
会自动选择最优模式，也支持手动指定。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List

from construction_agent.input.models import AccidentInput
from construction_agent.database.repository import PdfProcessingRecord

logger = logging.getLogger(__name__)

# ── 文件大小阈值 ──
FLASH_MAX_SIZE = 10 * 1024 * 1024  # 10MB，flash 模式上限
# flash 模式还有 20 页限制，但页数无法提前知晓，故以文件大小作为启发式阈值

# ── 段落分割阈值 ──
MIN_SEGMENT_CHARS = 50  # 小于此长度的段落视为噪声丢弃


class PDFProcessor:
    """MinerU PDF 事故文档处理器。

    自动处理文字型 PDF 和扫描型 PDF，无需手动区分。
    输出标准化的 List[AccidentInput] 供后续提取模块消费。

    Usage:
        # 默认模式（自动选择 flash/precision，小文件用 flash）
        processor = PDFProcessor()
        accidents = processor.process("事故通报_2024.pdf")

        # 带 Token（支持大文件 precision 模式）
        processor = PDFProcessor(token="your-mineru-token")
        accidents = processor.process("large_report.pdf", mode="precision")

        # 批量处理
        results = processor.process_batch(["a.pdf", "b.pdf"])
    """

    def __init__(self, token: str | None = None):
        """初始化 PDF 处理器。

        Args:
            token: MinerU API Token（precision 模式需要）。
                   留空时仅能使用 flash 模式（≤10MB/20页）。
                   获取地址: https://mineru.net/apiManage/token
        """
        self._token = token
        self._client = None  # 懒初始化

    @property
    def has_token(self) -> bool:
        """是否配置了 Token（可用 precision 模式）。"""
        return bool(self._token)

    @property
    def client(self):
        """延迟初始化 MinerU 客户端。"""
        if self._client is None:
            from mineru import MinerU

            self._client = MinerU(self._token) if self._token else MinerU()
        return self._client

    # ── 公开方法 ──

    def process(
        self,
        pdf_path: str | Path,
        mode: str = "auto",
        language: str = "ch",
        enable_table: bool = True,
        repository=None,
    ) -> List[AccidentInput]:
        """处理单个 PDF 文件，提取事故文本片段。

        Args:
            pdf_path: PDF 文件路径
            mode: 解析模式 — "auto"（自动选择）/ "flash" / "precision"
            language: 文档语言代码，默认 "ch"（中文）
            enable_table: 是否启用表格识别
            repository: 数据仓库（可选，用于写入 PDF 处理日志）

        Raises:
            FileNotFoundError: PDF 文件不存在
            ValueError: 不支持的文件格式或参数错误
            RuntimeError: 解析失败
        """
        pdf_path = Path(pdf_path)

        # 1. 文件校验
        self._validate_file(pdf_path)

        # 2. 确定模式
        selected_mode = self._resolve_mode(pdf_path, mode)

        # 3. MinerU 解析
        markdown_text = self._parse_with_mineru(
            pdf_path, selected_mode, language, enable_table
        )

        # 4. 清理 markdown
        cleaned_text = self._clean_markdown(markdown_text)

        # 5. 段落分割（优先尝试书籍案例格式）
        segments = self._split_book_cases(cleaned_text)
        if len(segments) < 3:
            # 非书籍格式，回退到通用分割
            segments = self._split_into_accidents(cleaned_text)

        # 6. 构建 AccidentInput
        source_name = pdf_path.name
        results = []
        for i, seg in enumerate(segments):
            results.append(
                AccidentInput(
                    text=seg,
                    source=source_name,
                    page_range="",  # 单个段落难以精确对应页码
                    metadata={
                        "mode": selected_mode,
                        "segment_index": i,
                        "total_segments": len(segments),
                    },
                )
            )

        logger.info(f"PDF {pdf_path.name}: 解析完成，提取 {len(results)} 个文本片段 (模式: {selected_mode})")

        # 记录 PDF 处理日志
        if repository:
            try:
                repository.log_pdf_processing(PdfProcessingRecord(
                    pdf_filename=pdf_path.name,
                    mode=selected_mode,
                    mineru_raw_output=markdown_text[:50000],
                    cleaned_text=cleaned_text[:20000],
                    segment_count=len(segments),
                    segments_discarded=0,
                    split_strategy_used="book_cases" if len(segments) >= 3 else "generic",
                    success=True, duration_ms=0,
                ))
            except Exception:
                pass

        return results

    def process_batch(
        self,
        pdf_paths: List[str | Path],
        mode: str = "auto",
        language: str = "ch",
    ) -> List[List[AccidentInput]]:
        """批量处理多个 PDF。

        Args:
            pdf_paths: PDF 文件路径列表
            mode: 解析模式
            language: 文档语言

        Returns:
            List[List[AccidentInput]]: 每个 PDF 对应一个片段列表
        """
        results = []
        for pp in pdf_paths:
            try:
                results.append(self.process(pp, mode=mode, language=language))
            except Exception as e:
                logger.error(f"处理失败 [{pp}]: {e}")
                results.append([])
        return results

    # ── 私有方法 ──

    @staticmethod
    def _validate_file(pdf_path: Path):
        """校验 PDF 文件。"""
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if pdf_path.suffix.lower() not in (".pdf", ".PDF"):
            raise ValueError(f"不支持的文件格式: {pdf_path.suffix}，仅支持 .pdf 文件")
        if pdf_path.stat().st_size == 0:
            raise ValueError(f"PDF 文件为空: {pdf_path}")

    def _resolve_mode(self, pdf_path: Path, mode: str) -> str:
        """确定实际使用的解析模式。"""
        if mode == "auto":
            file_size = pdf_path.stat().st_size
            if file_size <= FLASH_MAX_SIZE:
                logger.info(f"文件 {file_size / 1024:.0f}KB ≤ 10MB，使用 flash 模式（免认证）")
                return "flash"
            elif self.has_token:
                logger.info(f"文件 {file_size / 1024 / 1024:.1f}MB > 10MB，使用 precision 模式")
                return "precision"
            else:
                raise RuntimeError(
                    f"文件过大 ({file_size / 1024 / 1024:.1f}MB > 10MB)，"
                    f"flash 模式不支持。请设置 MINERU_API_TOKEN 环境变量以启用 precision 模式。\n"
                    f"获取 Token: https://mineru.net/apiManage/token"
                )

        if mode == "flash":
            # 用户手动指定 flash，检查文件大小
            file_size = pdf_path.stat().st_size
            if file_size > FLASH_MAX_SIZE:
                logger.warning(
                    f"文件 {file_size / 1024 / 1024:.1f}MB 超过 flash 模式限制（10MB），"
                    f"可能失败。建议使用 precision 模式。"
                )
            return "flash"

        if mode == "precision":
            if not self.has_token:
                raise ValueError(
                    "precision 模式需要 MinerU API Token。"
                    "请在环境变量中设置 MINERU_API_TOKEN 或通过 PDFProcessor(token=...) 传入。"
                )
            return "precision"

        raise ValueError(f"未知模式: {mode}，可选值: auto / flash / precision")

    def _parse_with_mineru(
        self,
        pdf_path: Path,
        mode: str,
        language: str = "ch",
        enable_table: bool = True,
    ) -> str:
        """调用 MinerU SDK 解析 PDF，返回 markdown 文本。

        Args:
            pdf_path: PDF 文件路径
            mode: "flash" 或 "precision"
            language: 文档语言
            enable_table: 是否启用表格识别

        Returns:
            str: 解析后的 markdown 文本

        Raises:
            RuntimeError: 解析失败（含错误码和详情）
        """
        pdf_str = str(pdf_path)

        if mode == "flash":
            logger.info(f"flash_extract: {pdf_path.name}")
            result = self.client.flash_extract(
                pdf_str,
                language=language,
                enable_table=enable_table,
                is_ocr=True,
            )
        else:
            # precision 模式：参数名不同 (ocr 不是 is_ocr, table 不是 enable_table)
            logger.info(f"precision extract (vlm): {pdf_path.name}")
            result = self.client.extract(
                pdf_str,
                model="vlm",
                language=language,
                table=enable_table,
                formula=True,
                ocr=True,
            )

        # 检查解析结果状态
        if result.state == "failed":
            raise RuntimeError(
                f"MinerU 解析失败 (err_code={result.err_code}): {result.error}"
            )
        if result.markdown is None:
            raise RuntimeError(
                f"MinerU 未返回内容 (state={result.state}, err_code={result.err_code})"
            )

        return result.markdown

    # ── 文本后处理 ──

    @staticmethod
    def _clean_markdown(md: str) -> str:
        """清理 MinerU 输出的 markdown 文本。

        - 移除 Markdown 图片语法（![](...)）
        - 移除 HTML 标签
        - 压缩过多连续空行
        - 移除仅含特殊字符的行
        """
        # 移除图片引用 ![](xxx) 和 ![alt](xxx)
        md = re.sub(r'!\[.*?\]\(.*?\)', '', md)

        # 移除 HTML 标签
        md = re.sub(r'<[^>]+>', '', md)

        # 压缩连续空行（3+ → 2）
        md = re.sub(r'\n{4,}', '\n\n\n', md)

        # 移除纯特殊字符行（如页码、分隔线等）
        lines = md.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            # 丢弃：纯数字行（可能是页码）、纯标点/空格行、过短全是符号的行
            if not stripped:
                cleaned_lines.append(line)
                continue
            # 保留有意义文本的行
            if re.match(r'^[\d\s\-=_#*~|]+$', stripped) and len(stripped) < 20:
                continue
            # 丢弃纯网址行
            if re.match(r'^https?://\S+$', stripped):
                continue
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    @staticmethod
    def _split_book_cases(text: str) -> List[str]:
        """将事故案例书籍分割为单个案例。

        专为《建筑施工安全事故案例分析》等中文事故案例书籍设计。
        以 ``## 一、事故简介`` 为锚点，向前回溯抓取案例标题（含无编号案例），
        确保每个案例片段包含完整的标题+事故简介+原因分析+事故教训+专家点评。

        参数：
            text: 书籍的 markdown 文本

        返回：
            List[str]: 案例文本片段列表
        """
        lines = text.split('\n')

        # 找到所有「## 一、事故简介」的位置作为案例锚点
        case_anchors = []
        for i, line in enumerate(lines):
            if re.match(r'^##\s*(?:一、|[0-9]+[\.、）\)]\s*)事故简介\s*$', line) or re.match(r'^##\s*\d+\s*事故\s*$', line):
                case_anchors.append(i)

        if not case_anchors:
            # 回退到通用分割
            logger.info("未找到书籍案例结构，回退到通用分割")
            return PDFProcessor._split_into_accidents(text)

        logger.info(f"检测到书籍格式，发现 {len(case_anchors)} 个案例")

        cases = []
        for idx, anchor in enumerate(case_anchors):
            # ── 向前回溯找案例标题 ──
            heading_lines = []
            start_line = anchor
            for back in range(1, 10):
                prev = anchor - back
                if prev < 0:
                    break
                prev_line = lines[prev].strip()
                # 上一篇的结束标记 → 停止回溯
                if re.match(r'^## 四、专家点评', prev_line):
                    break
                # Part 标题 → 停止回溯
                if re.match(r'^#{1,2}\s*第[一二三四五六七八九十]+部分', prev_line):
                    break
                # 目录/序/编委会 → 停止
                if prev_line in ('## 目录', '## 序', '## 本书编委会'):
                    break
                # 收集 ## 级别标题行作为案例标题
                if prev_line.startswith('## '):
                    heading_lines.insert(0, prev_line)
                    start_line = prev
                elif prev_line and not prev_line.startswith('#'):
                    # 非标题正文 → 没有更多标题行了
                    if not heading_lines:
                        pass  # 无标题案例，保持 start_line 不变
                    break

            # ── 确定结束行 ──
            if idx + 1 < len(case_anchors):
                next_anchor = case_anchors[idx + 1]
                # 回退到下一个案例标题之前
                end_line = next_anchor
                for back in range(1, 8):
                    check = next_anchor - back
                    if check > anchor and lines[check].strip().startswith('## 案例'):
                        end_line = check
                        break
                    if check > anchor and re.match(r'^#{1,2}\s*第[一二三四五六七八九十]+部分', lines[check].strip()):
                        end_line = check
                        break
            else:
                end_line = len(lines)

            case_text = '\n'.join(lines[start_line:end_line]).strip()
            if len(case_text) >= MIN_SEGMENT_CHARS:
                cases.append(case_text)

        logger.info(f"书籍案例分割: {len(cases)} 个案例")
        return cases

    @staticmethod
    def _split_into_accidents(text: str) -> List[str]:
        """将清理后的文本分割为单个事故片段。

        分割策略（两阶段）：
        1. 一级分割：连续 3+ 空行分隔（事故间通常有较大空白）
        2. 二级分割：标题模式 ^\\d+[、.．]\\s* 将一级段落进一步拆分

        参数：
            text: 清理后的文本

        返回：
            List[str]: 事故文本片段列表（已过滤短噪声）
        """
        # ── 一级分割：大段空白 ──
        raw_segments = re.split(r'\n{3,}', text)

        # ── 二级分割：标题模式 ──
        heading_pattern = re.compile(r'^(\d+)[、.．]\s*')
        final_segments = []

        for seg in raw_segments:
            sub_segs = heading_pattern.split(seg.strip())
            if not sub_segs or not sub_segs[0].strip():
                # 无标题匹配 → 整个作为一段
                if seg.strip():
                    final_segments.append(seg.strip())
                continue

            # sub_segs 形如 ['', '1', '事故内容', '2', '另一个事故', ...]
            # 第一个元素通常是空字符串（如果文本以标题开头）
            current = ""
            for i, part in enumerate(sub_segs):
                if i == 0:
                    # 首个元素：标题之前的内容（极少存在）
                    if part.strip():
                        current = part.strip()
                    continue
                if re.match(r'^\d+$', part):
                    # 编号 → 开始新段落
                    if current.strip():
                        final_segments.append(current.strip())
                    current = ""
                else:
                    # 事故文本内容
                    current += part

            # 提交最后一段
            if current.strip():
                final_segments.append(current.strip())

        # ── 过滤短噪声 ──
        result = []
        for seg in final_segments:
            # 去除多余空白
            cleaned = re.sub(r'[ \t]+', ' ', seg).strip()
            if len(cleaned) >= MIN_SEGMENT_CHARS:
                result.append(cleaned)
            else:
                logger.debug(f"丢弃短片段 ({len(cleaned)} 字符): {cleaned[:60]}...")

        logger.info(f"文本分割: {len(raw_segments)} 一级段 → {len(final_segments)} 二级段 → {len(result)} 有效段")

        return result
