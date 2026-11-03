from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from app.config import Settings
from app.services.extraction_service import extract_ocr_lines


class OCRRecognitionUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OCRRecognitionResult:
    fields: list[dict[str, Any]]
    warnings: list[str]
    attempts: int
    manual_required: bool


class OCRExtractionClient:
    """Local Chinese/English OCR for screenshots of Excel forms."""

    def __init__(self, config: Settings, engine: Any | None = None):
        self.config = config
        self._engine = engine
        self._engine_lock = Lock()
        self._last_error_code: str | None = None

    @property
    def enabled(self) -> bool:
        return self.config.ocr_enabled

    def public_status(self) -> dict[str, str | None]:
        if not self.enabled:
            return {"state": "manual_fallback", "last_error_code": "OCR_DISABLED"}
        if self._last_error_code:
            return {"state": "degraded", "last_error_code": self._last_error_code}
        available = self._engine is not None or importlib.util.find_spec("rapidocr") is not None
        return {
            "state": "available" if available else "unavailable",
            "last_error_code": None if available else "OCR_DEPENDENCY_MISSING",
        }

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                try:
                    from rapidocr import RapidOCR
                except ImportError as exc:
                    raise OCRRecognitionUnavailable("OCR_DEPENDENCY_MISSING", "本地 OCR 组件未安装") from exc
                self._engine = RapidOCR()
        return self._engine

    def _prepare_image(self, path: Path) -> tuple[np.ndarray, int, int]:
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, UnidentifiedImageError) as exc:
            raise OCRRecognitionUnavailable("INVALID_IMAGE", "图片无法读取") from exc

        original_width, original_height = image.size
        max_edge = max(image.size)
        if max_edge > self.config.ocr_image_max_edge:
            scale = self.config.ocr_image_max_edge / max_edge
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )

        gray = ImageOps.grayscale(image)
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = ImageEnhance.Contrast(gray).enhance(1.35)
        gray = gray.filter(ImageFilter.SHARPEN)
        prepared = gray.convert("RGB")
        return np.asarray(prepared), original_width, original_height

    def _normalize_result(self, result: Any, scale_x: float, scale_y: float) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        if hasattr(result, "txts"):
            boxes_value = getattr(result, "boxes", None)
            texts_value = getattr(result, "txts", None)
            scores_value = getattr(result, "scores", None)
            boxes = list(boxes_value) if boxes_value is not None else []
            texts = list(texts_value) if texts_value is not None else []
            scores = list(scores_value) if scores_value is not None else []
            items = zip(boxes, texts, scores)
        else:
            raw = result[0] if isinstance(result, tuple) else result
            items = (
                (item[0], item[1], item[2])
                for item in (raw or [])
                if isinstance(item, (list, tuple)) and len(item) >= 3
            )
        for box, text, score in items:
            points = np.asarray(box, dtype=float).reshape(-1, 2)
            if points.size < 8:
                continue
            x1, y1 = points.min(axis=0)
            x2, y2 = points.max(axis=0)
            lines.append({
                "text": str(text).strip(),
                "score": float(score),
                "box": [float(x1 * scale_x), float(y1 * scale_y), float(x2 * scale_x), float(y2 * scale_y)],
            })
        return lines

    def extract_image(self, path: Path, document_id: str, form_type: str) -> OCRRecognitionResult:
        if not self.enabled:
            raise OCRRecognitionUnavailable("OCR_DISABLED", "本地 OCR 未启用")
        try:
            image, original_width, original_height = self._prepare_image(path)
            result = self._get_engine()(image)
            scale_x = original_width / max(1, image.shape[1])
            scale_y = original_height / max(1, image.shape[0])
            lines = self._normalize_result(result, scale_x, scale_y)
            if not lines:
                raise OCRRecognitionUnavailable("OCR_EMPTY", "图片中没有识别到清晰文字")
            fields = extract_ocr_lines(
                lines,
                document_id=document_id,
                form_type=form_type,
                image_width=original_width,
                image_height=original_height,
            )
            missing = [item["path"] for item in fields if item.get("value_normalized") in (None, "")]
            warnings = [f"未识别字段：{field}" for field in missing]
            self._last_error_code = None
            return OCRRecognitionResult(fields, warnings, 1, bool(missing))
        except OCRRecognitionUnavailable as exc:
            self._last_error_code = exc.code
            raise
        except Exception as exc:
            self._last_error_code = "OCR_RUNTIME_ERROR"
            raise OCRRecognitionUnavailable("OCR_RUNTIME_ERROR", "本地 OCR 运行失败") from exc
