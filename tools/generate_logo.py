"""Generate the application logo (SVG, PNG and ICO) with no dependency.

The logo is a gold rating star with a download badge on a midnight blue
rounded square. Shapes are defined once, in a unit square, and rendered
both as SVG and as anti-aliased bitmaps.

Usage:
    python tools/generate_logo.py
"""

import math
import struct
import zlib
from collections.abc import Sequence
from pathlib import Path

ASSETS_DIR = (
    Path(__file__).resolve().parent.parent / "allocine_backup" / "assets"
)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLING = 4  # samples per pixel along each axis
SVG_SIZE = 512

Point = tuple[float, float]
Color = tuple[int, int, int]

BACKGROUND_TOP = (0x2B, 0x3D, 0x6B)
BACKGROUND_BOTTOM = (0x16, 0x20, 0x3A)
GOLD = (0xFF, 0xC8, 0x3D)
WHITE = (0xFF, 0xFF, 0xFF)
MIDNIGHT = (0x16, 0x20, 0x3A)

# Geometry in unit coordinates (0 to 1, y pointing down)
MARGIN = 0.03
CORNER_RADIUS = 0.2
STAR_CENTER = (0.5, 0.47)
STAR_RADIUS = 0.33
STAR_INNER_RATIO = 0.45
BADGE_CENTER = (0.73, 0.73)
BADGE_RADIUS = 0.17
# Ring of background colour that separates the badge from the star
BADGE_RING_WIDTH = 0.035
ARROW = [
    (0.705, 0.615), (0.755, 0.615), (0.755, 0.70), (0.80, 0.70),
    (0.73, 0.785), (0.66, 0.70), (0.705, 0.70),
]
TRAY = [(0.655, 0.81), (0.805, 0.81), (0.805, 0.845), (0.655, 0.845)]


def star_points() -> list[Point]:
    """Vertices of the five-pointed star, pointing up."""
    inner_radius = STAR_RADIUS * STAR_INNER_RATIO
    points = []
    for index in range(10):
        angle = math.radians(-90 + index * 36)
        radius = STAR_RADIUS if index % 2 == 0 else inner_radius
        points.append((
            STAR_CENTER[0] + radius * math.cos(angle),
            STAR_CENTER[1] + radius * math.sin(angle),
        ))
    return points


STAR = star_points()


def in_polygon(x: float, y: float, polygon: Sequence[Point]) -> bool:
    """Point-in-polygon test (ray casting)."""
    inside = False
    x1, y1 = polygon[-1]
    for x2, y2 in polygon:
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        x1, y1 = x2, y2
    return inside


def in_circle(x: float, y: float, center: Point, radius: float) -> bool:
    """Point-in-disc test."""
    return (x - center[0]) ** 2 + (y - center[1]) ** 2 <= radius ** 2


def in_rounded_square(x: float, y: float) -> bool:
    """Point-in-background test."""
    start = MARGIN + CORNER_RADIUS
    end = 1 - MARGIN - CORNER_RADIUS
    dx = max(start - x, 0, x - end)
    dy = max(start - y, 0, y - end)
    return dx * dx + dy * dy <= CORNER_RADIUS ** 2


def background_color(y: float) -> Color:
    """Vertical gradient of the background."""
    t = min(max((y - MARGIN) / (1 - 2 * MARGIN), 0), 1)
    return tuple(
        round(top + (bottom - top) * t)
        for top, bottom in zip(BACKGROUND_TOP, BACKGROUND_BOTTOM)
    )


def color_at(x: float, y: float) -> Color | None:
    """Colour of the logo at a point, or None where it is transparent.

    Shapes are tested from the topmost to the bottommost.
    """
    if not in_rounded_square(x, y):
        return None
    if in_circle(x, y, BADGE_CENTER, BADGE_RADIUS):
        if in_polygon(x, y, ARROW) or in_polygon(x, y, TRAY):
            return MIDNIGHT
        return WHITE
    if in_circle(x, y, BADGE_CENTER, BADGE_RADIUS + BADGE_RING_WIDTH):
        return background_color(y)
    if in_polygon(x, y, STAR):
        return GOLD
    return background_color(y)


def render(size: int) -> bytes:
    """Render the logo as RGBA pixels, anti-aliased by supersampling."""
    pixels = bytearray()
    sample_count = SUPERSAMPLING ** 2
    for row in range(size):
        for column in range(size):
            red = green = blue = covered = 0
            for sub_y in range(SUPERSAMPLING):
                for sub_x in range(SUPERSAMPLING):
                    color = color_at(
                        (column + (sub_x + 0.5) / SUPERSAMPLING) / size,
                        (row + (sub_y + 0.5) / SUPERSAMPLING) / size,
                    )
                    if color is not None:
                        red += color[0]
                        green += color[1]
                        blue += color[2]
                        covered += 1
            if covered:
                pixels += bytes((
                    round(red / covered),
                    round(green / covered),
                    round(blue / covered),
                    round(255 * covered / sample_count),
                ))
            else:
                pixels += bytes(4)
    return bytes(pixels)


def encode_png(size: int, pixels: bytes) -> bytes:
    """Encode square RGBA pixels as a PNG file."""
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I", crc)
        )

    stride = size * 4
    # Each scanline starts with its filter type (0: none)
    raw = b"".join(
        b"\x00" + pixels[row * stride:(row + 1) * stride]
        for row in range(size)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(images: Sequence[tuple[int, bytes]]) -> bytes:
    """Bundle PNG images of several sizes into a Windows icon."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory = b""
    for size, png in images:
        side = size if size < 256 else 0  # 0 means 256 in the ICO format
        directory += struct.pack(
            "<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset
        )
        offset += len(png)
    return header + directory + b"".join(png for _, png in images)


def build_svg() -> str:
    """Return the vector version of the logo."""
    def points(polygon: Sequence[Point]) -> str:
        return " ".join(
            f"{x * SVG_SIZE:.1f},{y * SVG_SIZE:.1f}" for x, y in polygon
        )

    def hex_color(color: Color) -> str:
        return "#{:02x}{:02x}{:02x}".format(*color)

    def scaled(value: float) -> str:
        return f"{value * SVG_SIZE:.1f}"

    badge_x, badge_y = (scaled(value) for value in BADGE_CENTER)
    return f"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_SIZE} {SVG_SIZE}">
  <defs>
    <linearGradient id="background" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{hex_color(BACKGROUND_TOP)}"/>
      <stop offset="1" stop-color="{hex_color(BACKGROUND_BOTTOM)}"/>
    </linearGradient>
  </defs>
  <rect x="{scaled(MARGIN)}" y="{scaled(MARGIN)}" \
width="{scaled(1 - 2 * MARGIN)}" height="{scaled(1 - 2 * MARGIN)}" \
rx="{scaled(CORNER_RADIUS)}" fill="url(#background)"/>
  <polygon points="{points(STAR)}" fill="{hex_color(GOLD)}"/>
  <circle cx="{badge_x}" cy="{badge_y}" \
r="{scaled(BADGE_RADIUS + BADGE_RING_WIDTH)}" fill="url(#background)"/>
  <circle cx="{badge_x}" cy="{badge_y}" r="{scaled(BADGE_RADIUS)}" \
fill="{hex_color(WHITE)}"/>
  <polygon points="{points(ARROW)}" fill="{hex_color(MIDNIGHT)}"/>
  <polygon points="{points(TRAY)}" fill="{hex_color(MIDNIGHT)}"/>
</svg>
"""


def main() -> None:
    """Write every version of the logo to the assets folder."""
    ASSETS_DIR.mkdir(exist_ok=True)
    pngs = {size: encode_png(size, render(size)) for size in ICO_SIZES}
    (ASSETS_DIR / "logo.svg").write_text(build_svg(), encoding="utf-8")
    (ASSETS_DIR / "logo.png").write_bytes(pngs[256])
    (ASSETS_DIR / "logo-64.png").write_bytes(pngs[64])
    (ASSETS_DIR / "logo.ico").write_bytes(encode_ico(sorted(pngs.items())))
    print(f"Logo written to {ASSETS_DIR}")


if __name__ == "__main__":
    main()
