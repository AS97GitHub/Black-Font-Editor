"""
black_texture.py - DXT1 / DXT3 / DXT5 (BC1 / BC2 / BC3) texture codecs and the DDS
loader used by the BLACK (Xbox) bitmap-font editor.

The font atlases are DXT3 (explicit 4-bit alpha); DXT5 is read as well, like the
launcher does.  Everything here works on RGBA PIL images.
"""

import os
import struct
from pathlib import Path

from PIL import Image


def rgb565(value: int) -> tuple[int, int, int]:
    """RGB565 -> RGB888 by bit replication (identical to the launcher's rgb565())."""
    r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def dxt_colours(c0: int, c1: int) -> tuple[tuple[int, int, int], ...]:
    """Four-colour BC palette, interpolants rounded like the launcher: (2a+b+1)/3, (a+2b+1)/3."""
    a, b = rgb565(c0), rgb565(c1)
    return (a, b,
            tuple((2 * a[n] + b[n] + 1) // 3 for n in range(3)),
            tuple((a[n] + 2 * b[n] + 1) // 3 for n in range(3)))


def decode_dxt3(data: bytes, width: int, height: int) -> Image.Image:
    """Decode the top DXT3/BC2 mip level used by the supplied font atlases."""
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    required = blocks_x * blocks_y * 16
    if len(data) < required:
        raise ValueError(f"DXT3 payload is truncated: need {required} bytes, have {len(data)}")
    image = Image.new("RGBA", (width, height))
    pixels = image.load()
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            alpha = int.from_bytes(data[pos:pos + 8], "little")
            c0, c1 = struct.unpack_from("<HH", data, pos + 8)
            colours = dxt_colours(c0, c1)
            indices = struct.unpack_from("<I", data, pos + 12)[0]
            for y in range(4):
                for x in range(4):
                    px, py = bx * 4 + x, by * 4 + y
                    if px < width and py < height:
                        i = y * 4 + x
                        r, g, b_ = colours[(indices >> (2 * i)) & 3]
                        pixels[px, py] = (r, g, b_, ((alpha >> (4 * i)) & 15) * 17)
            pos += 16
    return image


def _to_rgb565(r: int, g: int, b: int) -> int:
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def encode_dxt3(image: Image.Image) -> bytes:
    """Encode RGBA image to a single-level DXT3/BC2 payload (16 bytes per 4x4 block).

    Matches the layout used by stock Small.bin / Big.bin: explicit 4-bit alpha
    followed by a DXT1-style colour block with c0 >= c1 (four-colour mode).
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    width, height = image.size
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    pixels = image.load()
    out = bytearray(blocks_x * blocks_y * 16)
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = []
            for y in range(4):
                for x in range(4):
                    px, py = bx * 4 + x, by * 4 + y
                    if px < width and py < height:
                        block.append(pixels[px, py])
                    else:
                        block.append((0, 0, 0, 0))

            # Explicit 4-bit alpha (DXT3 / BC2)
            alpha_bits = 0
            for i, (_r, _g, _b, a) in enumerate(block):
                alpha_bits |= ((a * 15 + 127) // 255) << (4 * i)
            out[pos:pos + 8] = alpha_bits.to_bytes(8, "little")

            # Colour endpoints from opaque-ish texels (luminance extremes)
            opaque = [c[:3] for c in block if c[3] > 8]
            if not opaque:
                opaque = [(0, 0, 0)]

            def lum(c: tuple[int, int, int]) -> float:
                return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

            c0_rgb = min(opaque, key=lum)
            c1_rgb = max(opaque, key=lum)
            c0 = _to_rgb565(*c0_rgb)
            c1 = _to_rgb565(*c1_rgb)
            if c0 < c1:
                c0, c1 = c1, c0
            elif c0 == c1:
                c1 = max(0, c0 - 1)

            colours = dxt_colours(c0, c1)
            indices = 0
            for i, (r, g, bv, _a) in enumerate(block):
                best, best_d = 0, 1e18
                for j, col in enumerate(colours):
                    dr, dg, db = r - col[0], g - col[1], bv - col[2]
                    d = dr * dr + dg * dg + db * db
                    if d < best_d:
                        best_d, best = d, j
                indices |= best << (2 * i)
            struct.pack_into("<HHI", out, pos + 8, c0, c1, indices)
            pos += 16
    return bytes(out)


def _decode_dxt1(data: bytes, width: int, height: int) -> Image.Image:
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    required = blocks_x * blocks_y * 8
    if len(data) < required:
        raise ValueError(f"DXT1 payload truncated: need {required}, have {len(data)}")
    image = Image.new("RGBA", (width, height))
    pixels = image.load()
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            c0, c1 = struct.unpack_from("<HH", data, pos)
            a, b = rgb565(c0), rgb565(c1)
            if c0 > c1:
                colours = dxt_colours(c0, c1)
                alphas = (255, 255, 255, 255)
            else:
                colours = (a, b, tuple((a[n] + b[n]) // 2 for n in range(3)), (0, 0, 0))
                alphas = (255, 255, 255, 0)
            indices = struct.unpack_from("<I", data, pos + 4)[0]
            for y in range(4):
                for x in range(4):
                    px, py = bx * 4 + x, by * 4 + y
                    if px < width and py < height:
                        i = y * 4 + x
                        idx = (indices >> (2 * i)) & 3
                        r, g, b_ = colours[idx]
                        pixels[px, py] = (r, g, b_, alphas[idx])
            pos += 8
    return image


def _decode_dxt5(data: bytes, width: int, height: int) -> Image.Image:
    blocks_x, blocks_y = (width + 3) // 4, (height + 3) // 4
    required = blocks_x * blocks_y * 16
    if len(data) < required:
        raise ValueError(f"DXT5 payload truncated: need {required}, have {len(data)}")
    image = Image.new("RGBA", (width, height))
    pixels = image.load()
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            a0, a1 = data[pos], data[pos + 1]
            alpha_bits = int.from_bytes(data[pos + 2:pos + 8], "little")
            if a0 > a1:
                alpha_pal = [a0, a1] + [((6 - i) * a0 + (1 + i) * a1 + 3) // 7 for i in range(6)]
            else:
                alpha_pal = [a0, a1] + [((4 - i) * a0 + (1 + i) * a1 + 2) // 5 for i in range(4)] + [0, 255]
            c0, c1 = struct.unpack_from("<HH", data, pos + 8)
            colours = dxt_colours(c0, c1)
            indices = struct.unpack_from("<I", data, pos + 12)[0]
            for y in range(4):
                for x in range(4):
                    px, py = bx * 4 + x, by * 4 + y
                    if px < width and py < height:
                        i = y * 4 + x
                        r, g, b_ = colours[(indices >> (2 * i)) & 3]
                        alpha = alpha_pal[(alpha_bits >> (3 * i)) & 7]
                        pixels[px, py] = (r, g, b_, alpha)
            pos += 16
    return image


def load_dds_image(path: os.PathLike[str] | str) -> Image.Image:
    """Load a DDS texture. Supports DXT1/DXT3/DXT5 and uncompressed BGRA/BGR."""
    data = Path(path).read_bytes()
    if len(data) < 128 or data[:4] != b"DDS ":
        raise ValueError("Not a DDS file")
    height = struct.unpack_from("<I", data, 12)[0]
    width = struct.unpack_from("<I", data, 16)[0]
    pf_flags = struct.unpack_from("<I", data, 80)[0]
    fourcc = data[84:88]
    rgb_bit_count = struct.unpack_from("<I", data, 88)[0]
    header_size = 128
    if fourcc == b"DX10":
        if len(data) < 148:
            raise ValueError("Truncated DX10 DDS header")
        header_size = 148
        dxgi = struct.unpack_from("<I", data, 128)[0]
        if dxgi in (71, 72):
            fourcc = b"DXT1"
        elif dxgi in (74, 75):
            fourcc = b"DXT3"
        elif dxgi in (77, 78):
            fourcc = b"DXT5"
        else:
            raise ValueError(f"Unsupported DXGI format {dxgi}")
    payload = data[header_size:]
    if fourcc == b"DXT3":
        return decode_dxt3(payload, width, height)
    if fourcc == b"DXT1":
        return _decode_dxt1(payload, width, height)
    if fourcc == b"DXT5":
        return _decode_dxt5(payload, width, height)
    if pf_flags & 0x40:  # DDPF_RGB
        if rgb_bit_count == 32:
            return Image.frombytes("RGBA", (width, height),
                                   payload[: width * height * 4], "raw", "BGRA")
        if rgb_bit_count == 24:
            return Image.frombytes("RGB", (width, height),
                                   payload[: width * height * 3], "raw", "BGR").convert("RGBA")
    raise ValueError(f"Unsupported DDS format (FourCC={fourcc!r}, flags=0x{pf_flags:X})")
