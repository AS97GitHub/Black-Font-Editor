"""
black_font_format.py - reader/patcher for the BLACK (Xbox) bitmap-font files
Big.bin and Small.bin ("4.1v").

Field edits are applied in place to the in-memory file image; everything outside
the touched glyph records stays byte-identical, and the texture header, the
pointer table and any unrecognised bytes are preserved.  The atlas itself is a
single DXT3 / DXT5 level (see black_texture.py).
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from black_texture import _decode_dxt5, decode_dxt3, encode_dxt3

GLYPH_TABLE = 0x1C
GLYPH_RECORDS = 0x220
GLYPH_STRIDE = 0x28
TEXTURE_HEADER_SIZE = 0x80
MIN_FILE_SIZE = 0x300          # launcher: data.size() < 0x300 -> "Not a BLACK font file"
MAX_TEXTURE_SIDE = 2048        # launcher: kMaxTextureSide
DXT3_FORMAT = 0x0E             # launcher: DxtFormat::Dxt3 (Xbox, not swizzled)
DXT5_FORMAT = 0x0F             # launcher: DxtFormat::Dxt5
FORMAT_NAMES = {DXT3_FORMAT: "DXT3 / BC2", DXT5_FORMAT: "DXT5 / BC3"}


def display_character(code: int) -> str:
    """A visible list representation for a glyph character code."""
    if code == 0:
        return "□"
    if code == 0x20:
        return "\u02e2\u1d56"
    if code == 0xA0:
        return "\u207f\u1d47\u02e2\u1d56"
    if code == 0x3000:
        return "\u2071\u1d48\u02e2\u1d56"
    if 0xD800 <= code <= 0xDFFF:
        return "�"
    character = chr(code)
    return character if character.isprintable() else "·"


@dataclass
class Glyph:
    index: int
    offset: int
    u: float
    v: float
    width: float
    height: float
    bearing_x: float
    bearing_y: float
    advance: float
    code: int

    @property
    def label(self) -> str:
        # Character-first list entry; U+ code in parentheses for quick reading.
        # The full code remains editable in the Character property field.
        return f"{self.index:03d}  char={display_character(self.code)}  (U+{self.code:04X})"


class BlackFont:
    """A conservative reader/writer for the documented BLACK 4.1v font layout."""
    def __init__(self, path: os.PathLike[str] | str):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        if len(self.data) < MIN_FILE_SIZE or bytes(self.data[:4]) != b"4.1v":
            raise ValueError("Not a BLACK bitmap font (expected 4.1v at offset 0)")
        self.texture_offset = self.u32(4)
        if not GLYPH_RECORDS <= self.texture_offset <= len(self.data) - TEXTURE_HEADER_SIZE:
            raise ValueError(f"Invalid texture-header offset: 0x{self.texture_offset:X}")
        self.glyph_count = (self.texture_offset - GLYPH_RECORDS) // GLYPH_STRIDE
        # The stock files have a short preserved alignment tail after the final
        # complete record (0x18 in Big.bin, 0x08 in Small.bin).
        self.glyph_tail_bytes = (self.texture_offset - GLYPH_RECORDS) % GLYPH_STRIDE
        self.texture_format = self.u32(self.texture_offset + 0x34)
        self.texture_width = self.u32(self.texture_offset + 0x38)
        self.texture_height = self.u32(self.texture_offset + 0x3C)
        # The launcher always reads the pixels at texture header + 0x80 (it does not
        # follow the pointer stored at header + 4).
        self.pixel_offset = self.texture_offset + TEXTURE_HEADER_SIZE
        w, h = self.texture_width, self.texture_height
        if (not w or not h or w % 4 or h % 4
                or w > MAX_TEXTURE_SIDE or h > MAX_TEXTURE_SIDE):
            raise ValueError(f"Unsupported texture size {w} x {h}")
        if len(self.data) - self.pixel_offset < w * h:
            raise ValueError("Texture data is truncated")

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    @property
    def nominal_size(self) -> float:
        ratio = struct.unpack_from("<f", self.data, 0x0C)[0]
        return self.texture_height / ratio if math.isfinite(ratio) and ratio > 0 else 0.0

    def launcher_issues(self) -> list[str]:
        """Reasons the launcher's parseBitmapFont() would reject this font (empty = accepted)."""
        issues = []
        if not any(g.code for g in self.glyphs()):
            issues.append("no glyph with a non-zero character code")
        if not 1.0 < self.nominal_size < 200.0:
            issues.append(f"nominal size {self.nominal_size:.2f}px is outside (1, 200)")
        return issues

    @property
    def texture_name(self) -> str:
        raw = bytes(self.data[self.texture_offset + 0x44:self.texture_offset + 0x64])
        return raw.split(bytes([0]), 1)[0].decode("ascii", "replace")

    def glyph(self, index: int) -> Glyph:
        if not 0 <= index < self.glyph_count:
            raise IndexError(index)
        o = GLYPH_RECORDS + index * GLYPH_STRIDE
        u, v, w, h, bx, by, advance = struct.unpack_from("<7f", self.data, o)
        return Glyph(index, o, u, v, w, h, bx, by, advance, struct.unpack_from("<H", self.data, o + 0x1C)[0])

    def glyphs(self) -> list[Glyph]:
        return [self.glyph(i) for i in range(self.glyph_count)]

    def pointer(self, index: int) -> int:
        return self.u32(GLYPH_TABLE + 4 * index)

    def pointer_target(self, index: int) -> int | None:
        target = self.pointer(index)
        if target < GLYPH_RECORDS or target >= self.texture_offset:
            return None
        delta = target - GLYPH_RECORDS
        return delta // GLYPH_STRIDE if delta % GLYPH_STRIDE == 0 else None

    def write_glyph(self, glyph: Glyph) -> None:
        struct.pack_into("<7f", self.data, glyph.offset, glyph.u, glyph.v, glyph.width,
                         glyph.height, glyph.bearing_x, glyph.bearing_y, glyph.advance)
        struct.pack_into("<H", self.data, glyph.offset + 0x1C, glyph.code)

    def texture_image(self) -> Image.Image:
        """Decode the atlas. DXT3 (0x0E) and DXT5 (0x0F) are read, like the launcher."""
        payload = bytes(self.data[self.pixel_offset:])
        if self.texture_format == DXT3_FORMAT:
            return decode_dxt3(payload, self.texture_width, self.texture_height)
        if self.texture_format == DXT5_FORMAT:
            return _decode_dxt5(payload, self.texture_width, self.texture_height)
        raise ValueError(f"Unsupported texture format {self.texture_format}")

    def texture_payload_size(self) -> int:
        """Bytes for the top-level DXT3 mip (stock fonts store only this level)."""
        blocks_x = (self.texture_width + 3) // 4
        blocks_y = (self.texture_height + 3) // 4
        return blocks_x * blocks_y * 16

    def replace_texture(self, image: Image.Image) -> None:
        """Re-encode *image* as DXT3 and overwrite the atlas payload in-place.

        Width/height must match the existing texture header. Glyph records,
        pointer table and texture header are left unchanged (UV metrics stay
        valid because dimensions do not change).
        """
        if self.texture_format != DXT3_FORMAT:
            raise ValueError("Atlas replacement is only supported for DXT3/BC2 (0x0E) fonts; "
                             f"this font's texture format is 0x{self.texture_format:X} "
                             "(it can still be opened and its glyph metrics edited)")
        if image.size != (self.texture_width, self.texture_height):
            raise ValueError(
                f"Image size {image.size[0]}x{image.size[1]} does not match "
                f"atlas {self.texture_width}x{self.texture_height}"
            )
        payload = encode_dxt3(image)
        needed = self.texture_payload_size()
        if len(payload) != needed:
            raise ValueError(f"Encoded DXT3 size mismatch: got {len(payload)}, expected {needed}")
        end = self.pixel_offset + needed
        if end > len(self.data):
            self.data.extend(b"\x00" * (end - len(self.data)))
        self.data[self.pixel_offset:end] = payload

    def save(self, path: os.PathLike[str] | str) -> None:
        Path(path).write_bytes(self.data)
