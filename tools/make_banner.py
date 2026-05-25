"""Generate the GitHub social-preview banner.

Output: ./banner.png — 1280×640, the size GitHub asks for in
Settings → Social preview.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# GitHub social preview spec
W, H = 1280, 640

# Palette — dark navy with warm accent
BG_TOP = (15, 23, 42)       # slate-900
BG_BOT = (30, 41, 59)       # slate-800
ACCENT = (251, 146, 60)     # orange-400
WHITE = (248, 250, 252)
DIM = (148, 163, 184)       # slate-400
LINE = (51, 65, 85)         # slate-700


def find_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    """Pick the first font that loads. Fallback to default bitmap font."""
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# macOS / Linux font fallbacks
TITLE_FONT = find_font(
    ["/System/Library/Fonts/Supplemental/Futura.ttc",
     "/System/Library/Fonts/SFNSRounded.ttf",
     "/System/Library/Fonts/Helvetica.ttc",
     "DejaVuSans-Bold.ttf"],
    size=130,
)
SUB_FONT = find_font(
    ["/System/Library/Fonts/Supplemental/Futura.ttc",
     "/System/Library/Fonts/Helvetica.ttc",
     "DejaVuSans.ttf"],
    size=42,
)
META_FONT = find_font(
    ["/System/Library/Fonts/Menlo.ttc",
     "/System/Library/Fonts/Monaco.ttf",
     "DejaVuSansMono.ttf"],
    size=24,
)
TAGS_FONT = find_font(
    ["/System/Library/Fonts/Supplemental/Futura.ttc",
     "/System/Library/Fonts/Helvetica.ttc",
     "DejaVuSans.ttf"],
    size=26,
)


def vertical_gradient(img: Image.Image, top: tuple, bot: tuple) -> None:
    """Paint a top→bottom linear gradient onto img in-place."""
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        r = int(top[0] * (1 - t) + bot[0] * t)
        g = int(top[1] * (1 - t) + bot[1] * t)
        b = int(top[2] * (1 - t) + bot[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def draw_pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font) -> int:
    """Draw a rounded outlined chip. Returns the chip's right-edge x."""
    x, y = xy
    pad_x, pad_y = 18, 10
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    rect = (x, y, x + tw + 2 * pad_x, y + th + 2 * pad_y)
    draw.rounded_rectangle(rect, radius=22, outline=DIM, width=2)
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=WHITE)
    return rect[2]


def main() -> None:
    img = Image.new("RGB", (W, H), BG_TOP)
    vertical_gradient(img, BG_TOP, BG_BOT)
    draw = ImageDraw.Draw(img)

    # Decorative thin lines (suggest "trace" / pipeline)
    for i, alpha in enumerate([0.15, 0.25, 0.4]):
        y = 90 + i * 14
        draw.line([(60, y), (W - 60, y)], fill=LINE, width=1)

    # Top label — small kicker
    draw.text((80, 110), "AGENTS · MCP · A2A · LANGGRAPH", font=META_FONT, fill=ACCENT)

    # Headline
    draw.text((78, 170), "DeepBrief", font=TITLE_FONT, fill=WHITE)

    # Underline accent under headline
    draw.rectangle((80, 332, 240, 338), fill=ACCENT)

    # Subtitle
    draw.text((80, 360), "Build Your Own Deep Research", font=SUB_FONT, fill=DIM)

    # Tag chips row
    chips = ["MCP", "A2A", "ReAct", "Tool-RAG ready", "HITL", "FastMCP"]
    x = 80
    y = 470
    for c in chips:
        x = draw_pill(draw, (x, y), c, TAGS_FONT) + 12

    # Bottom-right meta block
    meta = [
        "12 notebooks   ·   2.9k LoC",
        "55 tests in 1.3s",
        "github.com/zyziyun/deepbrief-lab",
    ]
    for i, line in enumerate(meta):
        bbox = META_FONT.getbbox(line)
        tw = bbox[2] - bbox[0]
        draw.text((W - tw - 80, H - 110 + i * 32), line, font=META_FONT, fill=DIM)

    out = Path(__file__).parent.parent / "banner.png"
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
