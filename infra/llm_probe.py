from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.integrations import ImageRecognitionUnavailable, LLMExtractionClient  # noqa: E402


def probe_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def create_probe_image(path: Path) -> None:
    image = Image.new("RGB", (1500, 900), "white")
    draw = ImageDraw.Draw(image)
    title_font = probe_font(46)
    body_font = probe_font(34)
    small_font = probe_font(24)
    draw.rectangle((55, 55, 1445, 845), outline="#26364a", width=4)
    draw.text((420, 100), "项目决算明细表", fill="#17263a", font=title_font)
    rows = (
        ("项目名称", "模型连通性测试项目"),
        ("合同金额", "￥75,000.00"),
        ("支出总计", "￥5,000.00"),
        ("项目负责人", "测试人员"),
    )
    top = 220
    for index, (label, value) in enumerate(rows):
        y1 = top + index * 120
        y2 = y1 + 120
        draw.rectangle((140, y1, 1360, y2), outline="#67778a", width=2)
        draw.line((500, y1, 500, y2), fill="#67778a", width=2)
        draw.text((190, y1 + 35), label, fill="#26364a", font=body_font)
        draw.text((560, y1 + 35), value, fill="#111827", font=body_font)
    draw.text((420, 735), "仅用于视觉模型探针，不含真实业务数据", fill="#718096", font=small_font)
    image.save(path, format="PNG", optimize=True)


def main() -> int:
    if not settings.llm_base_url or not settings.llm_api_key:
        key_name = "ZAI_API_KEY" if settings.llm_provider == "bigmodel" else "LLM_API_KEY"
        print(f"FAILED: LLM_BASE_URL 或 {key_name} 未配置")
        return 2
    client = LLMExtractionClient(settings)
    catalog = client.check_model_catalog()
    catalog_state = (
        "available" if catalog.model_available is True
        else "not-listed" if catalog.model_available is False
        else "catalog-unsupported-or-unavailable"
    )
    print(f"CATALOG: reachable={catalog.reachable}, model={catalog_state}, status={catalog.status_code}")
    if catalog.model_available is False:
        print("FAILED: LLM_MODEL 不在服务端模型清单中")
        return 2
    with tempfile.TemporaryDirectory() as temporary:
        image_path = Path(temporary) / "monthly_award_llm_probe.png"
        create_probe_image(image_path)
        try:
            result = client.extract_image(
                image_path,
                "synthetic-probe-document",
                "PROJECT_SETTLEMENT",
            )
        except ImageRecognitionUnavailable as exc:
            print(f"FAILED: {exc.code} - {exc.message}")
            return 2
    print(f"OK: model={settings.llm_model}, attempts={result.attempts}, manual_required={result.manual_required}")
    for field in result.fields:
        print(f"- {field['path']}: {field['value_normalized']!r} (confidence={field['confidence']})")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    return 2 if result.manual_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
