#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标 PNG（纯标准库，不引入 Pillow）。

图形与页面 favicon 的内联 SVG 一致：圆角方块 + 薄荷→紫的对角渐变 + 深色书签。
改了配色或形状后重新运行：

    python3 tools/make_icons.py

输出（覆盖写入 static/）：
    icon-192.png           普通图标
    icon-512.png           普通图标（高分屏 / 安装引导）
    icon-maskable-512.png  Android 自适应图标：满幅背景 + 图形收进安全区
    apple-touch-icon.png   iOS 添加到主屏（180，无圆角，系统自己裁）
"""
import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "static"

GRAD_FROM = (0x7E, 0xE0, 0xC3)   # --accent 薄荷
GRAD_TO = (0x8D, 0x84, 0xFF)     # 品牌紫
INK = (0x07, 0x1B, 0x15)         # --accent-ink 深墨
SS = 3                           # 超采样倍数：够抗锯齿，又不至于让纯 Python 跑太久


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _in_round_rect(x, y, size, radius):
    """圆角矩形命中判定（坐标已归一到 [0, size)）。"""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def _in_bookmark(x, y, size, inset):
    """书签图形：上方圆角矩形，底边挖一个 V 形缺口。"""
    left = size * inset
    right = size * (1 - inset)
    top = size * (inset + 0.02)
    bottom = size * (1 - inset - 0.02)
    if not (left <= x <= right and top <= y <= bottom):
        return False
    # 顶部两角略作圆角，避免边缘过硬。
    r = (right - left) * 0.16
    if y < top + r:
        if x < left + r and (x - (left + r)) ** 2 + (y - (top + r)) ** 2 > r * r:
            return False
        if x > right - r and (x - (right - r)) ** 2 + (y - (top + r)) ** 2 > r * r:
            return False
    # 底部 V 形缺口：中线最深，两端不切。
    half = (right - left) / 2
    center = (left + right) / 2
    depth = (bottom - top) * 0.34
    cut_from = bottom - depth * (1 - abs(x - center) / half)
    return y < cut_from


def render(size, maskable=False):
    """返回 size×size 的 RGBA 行数据（每行 bytearray，长度 size*4）。"""
    radius = 0.0 if maskable else size * 0.22
    # 自适应图标的安全区是内接圆（约 80%），图形要再往里收一点。
    inset = 0.30 if maskable else 0.24
    rows = []
    samples = SS * SS
    for py in range(size):
        row = bytearray()
        for px in range(size):
            bg_hits = 0
            ink_hits = 0
            grad_acc = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if not (maskable or _in_round_rect(x, y, size, radius)):
                        continue
                    bg_hits += 1
                    grad_acc += (x + y) / (2.0 * size)
                    if _in_bookmark(x, y, size, inset):
                        ink_hits += 1
            if not bg_hits:
                row += b"\x00\x00\x00\x00"
                continue
            base = _lerp(GRAD_FROM, GRAD_TO, grad_acc / bg_hits)
            ink_ratio = ink_hits / float(bg_hits)
            color = _lerp(base, INK, ink_ratio)
            alpha = int(round(255 * bg_hits / float(samples)))
            row += bytes((color[0], color[1], color[2], alpha))
        rows.append(row)
    return rows


def write_png(path, rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    height = len(rows)
    width = len(rows[0]) // 4

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)
    return len(png)


def main():
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, True),
    ]
    for name, size, maskable in targets:
        path = OUT_DIR / name
        written = write_png(path, render(size, maskable))
        print("%-24s %4dx%-4d %6d bytes" % (name, size, size, written))


if __name__ == "__main__":
    main()
