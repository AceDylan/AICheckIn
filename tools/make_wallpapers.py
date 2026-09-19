#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成首页内置壁纸（WebP）。

素材来源：全部由本脚本程序化绘制（渐变、光斑、山脊线、噪点），不含任何第三方照片或
图库素材，因此不存在授权问题，可随仓库一起分发。固定随机种子，重复运行得到同样的画面。

只是开发期工具：需要 Pillow（带 WebP 支持），运行时镜像不依赖它。

    python3 tools/make_wallpapers.py

输出（覆盖写入 static/wallpapers/）：
    <id>.webp         桌面横版 2560x1440
    <id>-m.webp       手机竖版 1080x1920
    <id>-thumb.webp   选择器缩略图 320x180
    manifest.json     每张壁纸的主色与亮度（p90 相对亮度，页面据此决定遮罩至少压多暗）

体积预算（tests/test_home_wallpaper.py 会核对）：横版 ≤ 320KB，竖版 ≤ 200KB，缩略图 ≤ 12KB。
"""
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "wallpapers"
DESKTOP = (2560, 1440)
MOBILE = (1080, 1920)
THUMB = (320, 180)
FIELD_LONG_SIDE = 320      # 渐变 / 光斑先在低分辨率上逐像素计算，再平滑放大


def _lerp(a, b, t):
    return a + (b - a) * t


def _mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(_lerp(c1[i], c2[i], t) for i in range(3))


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _gradient(stops, t):
    """stops: [(pos, (r,g,b))]，pos 升序，t∈[0,1]。"""
    t = max(0.0, min(1.0, t))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            return _mix(c0, c1, _smooth((t - p0) / (p1 - p0) if p1 > p0 else 0))
    return stops[-1][1]


def _field(size, shader):
    """在低分辨率网格上跑 shader(u, v, aspect) -> (r,g,b)，再双三次放大到目标尺寸。"""
    w, h = size
    scale = FIELD_LONG_SIDE / float(max(w, h))
    fw, fh = max(2, int(round(w * scale))), max(2, int(round(h * scale)))
    aspect = w / float(h)
    img = Image.new("RGB", (fw, fh))
    px = img.load()
    for y in range(fh):
        v = y / float(fh - 1)
        for x in range(fw):
            u = x / float(fw - 1)
            r, g, b = shader(u, v, aspect)
            px[x, y] = (int(max(0, min(255, r))), int(max(0, min(255, g))), int(max(0, min(255, b))))
    # 模糊半径要盖过一个低分辨率格子（约 8px），否则放大后的格子边界在暗部会显成网格。
    return img.resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(max(w, h) / 160.0))


def _blob(u, v, aspect, cx, cy, rx, ry):
    dx = (u - cx) * aspect / rx
    dy = (v - cy) / ry
    return math.exp(-(dx * dx + dy * dy))


def _grain(img, amount, seed):
    """薄薄一层噪点：平滑渐变经有损压缩容易出色带，噪点能把它打散。"""
    random.seed(seed)
    noise = Image.effect_noise(img.size, 22).convert("RGB")
    return Image.blend(img, ImageChops.overlay(img, noise), amount)


def _ridge_points(rng, width, base, amp, rough, step):
    """一条山脊线：几组正弦叠加 + 小幅随机扰动。返回 [(x, y)]，y 为自顶向下的像素。"""
    waves = [(rng.uniform(0.6, 1.4) / (k + 1), rng.uniform(0.8, 2.2) * (k + 1), rng.uniform(0, math.tau)) for k in range(5)]
    pts, jitter = [], 0.0
    for x in range(0, width + step, step):
        t = x / float(width)
        y = sum(a * math.sin(f * math.tau * t + p) for a, f, p in waves)
        jitter = jitter * 0.72 + rng.uniform(-1, 1) * rough
        pts.append((x, base + (y * 0.5 + jitter) * amp))
    return pts


def _ridge_layer(img, rng, base, amp, rough, color, haze=None):
    """把一层山体叠到 img 上；2 倍超采样画蒙版换抗锯齿。haze=(color, strength) 给山体加自下而上的雾。"""
    w, h = img.size
    mask = Image.new("L", (w * 2, h * 2), 0)
    pts = _ridge_points(rng, w * 2, base * 2, amp * 2, rough, 8)
    ImageDraw.Draw(mask).polygon(pts + [(w * 2, h * 2), (0, h * 2)], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    layer = Image.new("RGB", (w, h), tuple(int(c) for c in color))
    if haze:
        haze_color, strength = haze
        column = Image.new("L", (1, h))
        top = int(base - amp)
        for y in range(h):
            t = (y - top) / float(max(1, h - top))
            column.putpixel((0, y), int(255 * strength * _smooth(t)))
        layer = Image.composite(Image.new("RGB", (w, h), tuple(int(c) for c in haze_color)), layer, column.resize((w, h)))
    img.paste(layer, (0, 0), mask)


# ---------- 三张壁纸 ----------

def aurora(size, seed=11):
    """极光：深海军蓝夜空 + 青绿 / 紫色光幕 + 星点。"""
    rng = random.Random(seed)
    sky = [(0.0, (4, 8, 22)), (0.55, (8, 22, 46)), (1.0, (10, 34, 52))]
    # 光幕：横向拉得很开的扁光斑，强度压低——时钟和搜索框就落在这一带，太亮会抢字。
    curtains = [(0.30, 0.30, 0.62, 0.085, (36, 200, 160)), (0.64, 0.38, 0.50, 0.075, (64, 140, 225)),
                (0.80, 0.27, 0.34, 0.060, (140, 92, 215)), (0.12, 0.44, 0.40, 0.060, (30, 176, 190))]

    def shader(u, v, aspect):
        r, g, b = _gradient(sky, v)
        for cx, cy, rx, ry, c in curtains:
            wave = 0.05 * math.sin(u * 7.0 + cx * 20.0) + 0.02 * math.sin(u * 19.0 + cy * 9.0)
            k = 0.30 * _blob(u, v + wave, aspect, cx, cy, rx * aspect, ry)
            # 光幕向上拖出一道更淡的余晖
            k += 0.12 * _blob(u, v + wave + 0.10, aspect, cx, cy, rx * aspect, ry * 2.6)
            r, g, b = r + c[0] * k, g + c[1] * k, b + c[2] * k
        return r, g, b

    img = _field(size, shader)
    w, h = size
    stars = Image.new("L", size, 0)
    draw = ImageDraw.Draw(stars)
    for _ in range(int(w * h / 5200)):
        x, y = rng.uniform(0, w), rng.uniform(0, h * 0.72)
        rad = rng.choice((0.6, 0.8, 1.0, 1.0, 1.4, 2.0)) * max(w, h) / 2560.0
        draw.ellipse((x - rad, y - rad, x + rad, y + rad), fill=int(rng.uniform(70, 230) * (1 - y / (h * 0.9))))
    img.paste(Image.new("RGB", size, (235, 244, 255)), (0, 0), stars.filter(ImageFilter.GaussianBlur(0.6)))
    _ridge_layer(img, rng, h * 0.90, h * 0.05, 0.10, (5, 12, 24))
    return _grain(img, 0.10, seed)


def dusk(size, seed=23):
    """暮色群山：靛蓝天顶 → 暖橙地平线，五层山脊由远及近逐层变深。"""
    rng = random.Random(seed)
    w, h = size
    sky = [(0.0, (18, 20, 58)), (0.38, (62, 40, 98)), (0.58, (150, 66, 96)), (0.70, (214, 112, 78)), (1.0, (230, 150, 96))]
    sun = (0.68, 0.64)

    def shader(u, v, aspect):
        r, g, b = _gradient(sky, v / 0.78)
        k = 0.55 * _blob(u, v, aspect, sun[0], sun[1], 0.30 * aspect, 0.20) + 0.5 * _blob(u, v, aspect, sun[0], sun[1], 0.07 * aspect, 0.05)
        return r + 120 * k, g + 70 * k, b + 30 * k

    img = _field(size, shader)
    layers = [(0.60, 0.06, (120, 74, 112)), (0.68, 0.07, (88, 56, 98)), (0.76, 0.08, (58, 40, 80)),
              (0.85, 0.08, (34, 26, 56)), (0.94, 0.07, (16, 14, 32))]
    for base, amp, color in layers:
        _ridge_layer(img, rng, h * base, h * amp, 0.16, color, haze=((150, 84, 104), 0.30 if base < 0.8 else 0.0))
    return _grain(img, 0.09, seed)


def mist(size, seed=37):
    """晨雾山林：青绿色层叠丘陵 + 雾带，整体偏冷、偏暗。"""
    rng = random.Random(seed)
    w, h = size
    sky = [(0.0, (10, 30, 40)), (0.45, (30, 72, 80)), (0.75, (84, 132, 128)), (1.0, (120, 160, 150))]

    def shader(u, v, aspect):
        r, g, b = _gradient(sky, v / 0.7)
        k = 0.5 * _blob(u, v, aspect, 0.28, 0.42, 0.34 * aspect, 0.24)
        return r + 70 * k, g + 80 * k, b + 66 * k

    img = _field(size, shader)
    layers = [(0.52, 0.07, (56, 104, 104)), (0.62, 0.08, (38, 84, 88)), (0.72, 0.08, (24, 64, 70)),
              (0.83, 0.08, (14, 44, 52)), (0.93, 0.07, (7, 26, 34))]
    for base, amp, color in layers:
        _ridge_layer(img, rng, h * base, h * amp, 0.13, color, haze=((120, 164, 158), 0.42 if base < 0.8 else 0.12))
    return _grain(img, 0.09, seed)


WALLPAPERS = [("aurora", "极光", aurora), ("dusk", "暮色群山", dusk), ("mist", "晨雾山林", mist)]


# ---------- 统计与输出 ----------

def _rel_luminance(rgb):
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def stats(img):
    """主色（整图平均色）与 p90 相对亮度：页面按它算「白字要 4.5:1，遮罩至少多暗」。"""
    small = img.convert("RGB").resize((64, 36), Image.BOX)
    raw = small.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    lums = sorted(_rel_luminance(p) for p in pixels)
    avg = tuple(int(round(sum(p[i] for p in pixels) / float(len(pixels)))) for i in range(3))
    return {"color": "#%02x%02x%02x" % avg, "lum": round(lums[int(len(lums) * 0.9)], 3)}


def _save(img, path, quality, budget):
    """按体积预算逐级降质量保存；返回最终字节数。"""
    while True:
        img.save(path, "WEBP", quality=quality, method=6)
        size = path.stat().st_size
        if size <= budget or quality <= 50:
            return size
        quality -= 6


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for wid, name, painter in WALLPAPERS:
        desktop, mobile = painter(DESKTOP), painter(MOBILE)
        info = stats(desktop)
        # 竖版构图不同，亮度取两者中更亮的那个，遮罩按最坏情况算。
        info["lum"] = max(info["lum"], stats(mobile)["lum"])
        sizes = {
            "desktop": _save(desktop, OUT_DIR / (wid + ".webp"), 82, 320 * 1024),
            "mobile": _save(mobile, OUT_DIR / (wid + "-m.webp"), 80, 200 * 1024),
            "thumb": _save(desktop.resize(THUMB, Image.LANCZOS), OUT_DIR / (wid + "-thumb.webp"), 78, 12 * 1024),
        }
        manifest.append({"id": wid, "name": name, "color": info["color"], "lum": info["lum"]})
        print("%-7s color=%s lum=%.3f  %s" % (wid, info["color"], info["lum"],
                                                "  ".join("%s=%dKB" % (k, v // 1024) for k, v in sizes.items())))
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
