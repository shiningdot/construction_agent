"""CLIP 图文一致性过滤器 —— 主管道的图片净化环节。

作用：从候选图片中剔除网站 logo、二维码、图标等非事故现场照片，
      只有通过过滤的图片才进入后续的 VLM 分析。

模型：openai/clip-vit-base-patch32（transformers 加载）
判据：构造正 / 负文本对 ——
      正类「施工现场事故现场照片」 vs 负类「网站logo或二维码或图标」
      取正类 softmax 概率，> 阈值（默认 0.5）判为事故现场照片。
      阈值由 data/clip_test/ 的 keep / reject 人工判读样本校准。

设计约束：
- 延迟加载：仅在实际过滤时才导入 transformers 并加载权重，
  使不涉及图片的管道流程不必依赖 torch。
- 优雅降级：模型加载失败时记警告并放行全部图片，不阻断主管道。
- 国内网络：默认走 HF 镜像，避免权重下载超时。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "openai/clip-vit-base-patch32"
DEFAULT_POSITIVE_TEXT = "施工现场事故现场照片"
DEFAULT_NEGATIVE_TEXT = "网站logo或二维码或图标"
DEFAULT_THRESHOLD = 0.5
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class CLIPFilter:
    """CLIP 图文一致性过滤器。

    用法:
        clip = CLIPFilter()
        if clip.is_accident_photo("a.jpg") is not False:
            ...   # True = 事故照片；None = 模型不可用（放行）

        stats = clip.filter_dir("candidates/", keep_dir="imageV3/",
                                reject_dir="clip_test/reject/")
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        threshold: float = DEFAULT_THRESHOLD,
        positive_text: str = DEFAULT_POSITIVE_TEXT,
        negative_text: str = DEFAULT_NEGATIVE_TEXT,
        hf_endpoint: Optional[str] = DEFAULT_HF_ENDPOINT,
    ):
        self.model_name = model_name
        self.threshold = threshold
        self.texts = [positive_text, negative_text]
        self._model = None
        self._processor = None
        self._failed = False
        if hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", hf_endpoint)

    # ══════════════════════════════════════════════════════════
    # 模型加载（延迟 + 失败缓存）
    # ══════════════════════════════════════════════════════════

    def _load(self) -> bool:
        """懒加载 CLIP 模型，返回是否可用。"""
        if self._model is not None:
            return True
        if self._failed:
            return False
        try:
            from transformers import CLIPModel, CLIPProcessor

            logger.info("[CLIP] 加载模型 %s …", self.model_name)
            self._model = CLIPModel.from_pretrained(self.model_name)
            self._processor = CLIPProcessor.from_pretrained(self.model_name)
            self._model.eval()
            logger.info("[CLIP] 模型就绪（阈值 %.2f）", self.threshold)
            return True
        except Exception as e:  # 依赖缺失 / 网络失败 / 权重损坏
            logger.warning("[CLIP] 模型加载失败，图片过滤将放行全部图片：%s", e)
            self._failed = True
            return False

    # ══════════════════════════════════════════════════════════
    # 判别
    # ══════════════════════════════════════════════════════════

    def score(self, img_path: str | Path) -> Optional[float]:
        """返回「事故现场照片」的正类概率。

        Returns:
            float —— 正类概率 [0, 1]
            None  —— 模型不可用或图片读取失败（调用方应放行）
        """
        if not self._load():
            return None
        try:
            import torch
            from PIL import Image

            with Image.open(img_path) as im:
                image = im.convert("RGB")

            inputs = self._processor(
                text=self.texts, images=image, return_tensors="pt", padding=True
            )
            with torch.no_grad():
                logits = self._model(**inputs).logits_per_image  # shape: (图数, 文本数)
            # 在文本维度上归一化，取第 0 个文本（正类）的概率
            probs = logits.softmax(dim=-1)
            return float(probs[0, 0].item())
        except Exception as e:
            logger.debug("[CLIP] 判别失败 %s: %s", img_path, e)
            return None

    def is_accident_photo(self, img_path: str | Path) -> Optional[bool]:
        """判定是否事故现场照片。

        Returns:
            True  —— 是，保留
            False —— 否，剔除
            None  —— 无法判定（模型不可用），调用方应放行
        """
        s = self.score(img_path)
        if s is None:
            return None
        return s > self.threshold

    # ══════════════════════════════════════════════════════════
    # 批量过滤
    # ══════════════════════════════════════════════════════════

    def filter_file(
        self,
        src: str | Path,
        keep_dir: Optional[str | Path] = None,
        reject_dir: Optional[str | Path] = None,
    ) -> bool:
        """过滤单个文件：按判定结果移动到 keep_dir 或 reject_dir。

        未指定目录时只做判定、不移动文件。

        Returns:
            True = 保留，False = 剔除
        """
        src = Path(src)
        keep = self.is_accident_photo(src) is not False  # None（不可用）按保留处理

        target = keep_dir if keep else reject_dir
        if target:
            dst_dir = Path(target)
            dst_dir.mkdir(parents=True, exist_ok=True)
            try:
                src.replace(dst_dir / src.name)
            except OSError as e:
                logger.debug("[CLIP] 移动失败 %s: %s", src, e)

        return keep

    def filter_dir(
        self,
        src_dir: str | Path,
        keep_dir: Optional[str | Path] = None,
        reject_dir: Optional[str | Path] = None,
        pattern: str = "*",
    ) -> dict:
        """过滤目录中的图片。

        Returns:
            {"total", "kept", "rejected", "undecided", "available"}
        """
        src_dir = Path(src_dir)
        files = [
            p for p in sorted(src_dir.glob(pattern))
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        ]

        available = self._load()
        stats = {"total": len(files), "kept": 0, "rejected": 0,
                 "undecided": 0, "available": available}

        if not available:
            stats["kept"] = len(files)
            stats["undecided"] = len(files)
            return stats

        for f in files:
            verdict = self.is_accident_photo(f)
            if verdict is None:
                stats["undecided"] += 1
                keep = True
            else:
                keep = verdict
                stats["kept" if keep else "rejected"] += 1

            target = keep_dir if keep else reject_dir
            if target:
                dst_dir = Path(target)
                dst_dir.mkdir(parents=True, exist_ok=True)
                try:
                    f.replace(dst_dir / f.name)
                except OSError as e:
                    logger.debug("[CLIP] 移动失败 %s: %s", f, e)

        logger.info(
            "[CLIP] 过滤完成：共 %d，保留 %d，剔除 %d，未判定 %d",
            stats["total"], stats["kept"], stats["rejected"], stats["undecided"],
        )
        return stats


# ══════════════════════════════════════════════════════════════
# 模块级单例：全管道共享一份模型，避免重复加载
# ══════════════════════════════════════════════════════════════

_default_filter: Optional[CLIPFilter] = None


def get_clip_filter(**kwargs) -> CLIPFilter:
    """获取全局共享的 CLIPFilter 实例。"""
    global _default_filter
    if _default_filter is None:
        _default_filter = CLIPFilter(**kwargs)
    return _default_filter


def is_accident_photo(img_path: str | Path) -> Optional[bool]:
    """便捷函数：用全局单例判定单张图片。"""
    return get_clip_filter().is_accident_photo(img_path)
