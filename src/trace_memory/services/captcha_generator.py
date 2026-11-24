"""Zero-dependency CSPRNG graphic CAPTCHA generator producing both SVG and PNG data URLs."""

from __future__ import annotations

import base64
import math
import secrets
import struct
import zlib

# Unambiguous uppercase alphanumeric characters (excludes 0, O, 1, I, L)
CAPTCHA_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_PALETTE_RGB: tuple[tuple[int, int, int], ...] = (
    (29, 78, 216),   # royal blue
    (124, 58, 237),  # violet
    (15, 118, 110),  # teal
    (190, 18, 60),   # crimson
    (194, 65, 12),   # burnt orange
)

# Clean 5x7 bitmap glyphs for unambiguous CAPTCHA_ALPHABET
_GLYPHS_5X7: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "01010", "00100", "00100", "00100", "01010", "10001"),
    "Y": ("10001", "01010", "00100", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
}


def generate_captcha_code(length: int = 4) -> str:
    return "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(max(4, length)))


def render_captcha_svg_data_url(code: str) -> str:
    width, height = 136, 44
    rng = secrets.SystemRandom()

    lines_svg: list[str] = []
    for _ in range(4):
        r, g, b = rng.choice(_PALETTE_RGB)
        x1, y1 = rng.randint(2, 32), rng.randint(6, height - 6)
        cx, cy = rng.randint(45, 92), rng.randint(2, height - 2)
        x2, y2 = rng.randint(width - 34, width - 2), rng.randint(6, height - 6)
        lines_svg.append(
            f'<path d="M{x1},{y1} Q{cx},{cy} {x2},{y2}" fill="none" '
            f'stroke="rgb({r},{g},{b})" stroke-opacity="0.32" stroke-width="1.6"/>'
        )

    dots_svg: list[str] = []
    for _ in range(18):
        r, g, b = rng.choice(_PALETTE_RGB)
        dx = rng.randint(4, width - 4)
        dy = rng.randint(4, height - 4)
        rad = rng.choice(("1.1", "1.5", "1.9"))
        dots_svg.append(
            f'<circle cx="{dx}" cy="{dy}" r="{rad}" fill="rgb({r},{g},{b})" fill-opacity="0.28"/>'
        )

    chars_svg: list[str] = []
    step = (width - 24) // max(1, len(code))
    for idx, ch in enumerate(code):
        r, g, b = rng.choice(_PALETTE_RGB)
        x = 18 + idx * step + rng.randint(-2, 2)
        y = 30 + rng.randint(-3, 3)
        rot = rng.randint(-15, 15)
        chars_svg.append(
            f'<text x="{x}" y="{y}" fill="rgb({r},{g},{b})" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
            f'font-size="23" font-weight="800" transform="rotate({rot} {x} {y - 7})">{ch}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" rx="10" fill="#eef2ff"/>'
        + "".join(lines_svg)
        + "".join(dots_svg)
        + "".join(chars_svg)
        + "</svg>"
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def render_captcha_png_data_url(code: str) -> str:
    width, height = 136, 44
    rng = secrets.SystemRandom()

    # Soft indigo-tinted background (#EEF2FF -> 238, 242, 255)
    buf = bytearray(width * height * 3)
    for i in range(0, len(buf), 3):
        buf[i] = 238
        buf[i + 1] = 242
        buf[i + 2] = 255

    def _set_px(px: int, py: int, rgb: tuple[int, int, int], alpha: float = 1.0) -> None:
        if 0 <= px < width and 0 <= py < height:
            off = (py * width + px) * 3
            if alpha >= 0.99:
                buf[off], buf[off + 1], buf[off + 2] = rgb
            else:
                inv = 1.0 - alpha
                buf[off] = int(buf[off] * inv + rgb[0] * alpha)
                buf[off + 1] = int(buf[off + 1] * inv + rgb[1] * alpha)
                buf[off + 2] = int(buf[off + 2] * inv + rgb[2] * alpha)

    # Interference sine waves
    for _ in range(3):
        col = rng.choice(_PALETTE_RGB)
        base_y = rng.uniform(10.0, height - 10.0)
        amp = rng.uniform(4.0, 9.0)
        freq = rng.uniform(0.05, 0.11)
        phase = rng.uniform(0.0, 6.28)
        for x in range(width):
            y = int(round(base_y + amp * math.sin(x * freq + phase)))
            _set_px(x, y, col, 0.42)
            _set_px(x, y + 1, col, 0.28)

    # Confetti noise dots
    for _ in range(32):
        col = rng.choice(_PALETTE_RGB)
        nx, ny = rng.randint(2, width - 3), rng.randint(2, height - 3)
        _set_px(nx, ny, col, 0.36)
        _set_px(nx + 1, ny, col, 0.25)

    # Draw 4 scaled 5x7 glyphs (scale=3 -> 15x21) with wave/shear jitter
    scale = 3
    step = (width - 20) // max(1, len(code))
    for idx, ch in enumerate(code):
        glyph = _GLYPHS_5X7.get(ch.upper())
        if not glyph:
            continue
        col = rng.choice(_PALETTE_RGB)
        origin_x = 12 + idx * step + rng.randint(-2, 2)
        origin_y = 11 + rng.randint(-3, 3)
        shear = rng.choice((-0.22, -0.12, 0.0, 0.12, 0.22))
        for gy, row_bits in enumerate(glyph):
            for gx, bit in enumerate(row_bits):
                if bit != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        dy = gy * scale + sy
                        dx = gx * scale + sx + int(round((dy - 10) * shear))
                        _set_px(origin_x + dx, origin_y + dy, col, 0.96)

    # Encode raw scanlines (filter byte 0 per row)
    raw_rows = bytearray()
    stride = width * 3
    for y in range(height):
        raw_rows.append(0)
        start = y * stride
        raw_rows.extend(buf[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), level=6)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
