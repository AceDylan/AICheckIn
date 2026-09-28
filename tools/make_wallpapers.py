#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成首页内置壁纸（WebP）。

素材来源：全部由本脚本程序化绘制（渐变、光斑、山脊线、值噪声、星点、噪点），不含任何第三方照片或
图库素材，因此不存在授权问题，可随仓库一起分发。固定随机种子，重复运行得到同样的画面。

只是开发期工具：需要 Pillow（带 WebP 支持），运行时镜像不依赖它。

    python3 tools/make_wallpapers.py                 # 全部重画
    python3 tools/make_wallpapers.py galaxy flow     # 只重画这几张
    python3 tools/make_wallpapers.py --out /tmp/wp   # 输出到别处预览

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


# ---------- 第一批：极光、暮色群山、晨雾山林 ----------

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


# ---------- 第二批（2026-09-28）：墨山、星河、海湾、流光 ----------

class _Noise:
    """值噪声：在 (cells+2)² 的随机格点上做平滑插值；几层叠起来就是云气 / 星尘那种不规则的明暗。"""

    def __init__(self, seed, cells):
        rng = random.Random(seed)
        self.cells = cells
        self.grid = [[rng.random() for _ in range(cells + 2)] for _ in range(cells + 2)]

    def __call__(self, x, y):
        """x, y ∈ [0, 1]（超出按边缘夹住）。返回 [0, 1]。"""
        c = self.cells
        x = max(0.0, min(0.999999, x)) * c
        y = max(0.0, min(0.999999, y)) * c
        ix, iy = int(x), int(y)
        fx, fy = _smooth(x - ix), _smooth(y - iy)
        g = self.grid
        top = _lerp(g[iy][ix], g[iy][ix + 1], fx)
        bottom = _lerp(g[iy + 1][ix], g[iy + 1][ix + 1], fx)
        return _lerp(top, bottom, fy)


def _fbm(layers, x, y):
    """layers: [(噪声, 权重)]，权重之和约为 1。"""
    return sum(n(x, y) * wgt for n, wgt in layers)


def _fbm_layers(seed, base_cells=4, octaves=4):
    return [(_Noise(seed + k, base_cells * (2 ** k)), 0.5 ** (k + 1) / (1 - 0.5 ** octaves)) for k in range(octaves)]


def _peak_points(rng, width, base, amp, step, sharp=0.6, waves=6, jitter=0.06):
    """带尖峰的山脊：|sin| 叠加出棱角分明的山头，再掺一点抖动。和 _ridge_points 一样返回 [(x, y)]。"""
    comps = [(rng.uniform(0.5, 1.2) / (k + 1) ** 0.8, rng.uniform(1.0, 2.4) * (k + 1), rng.uniform(0, math.tau)) for k in range(waves)]
    pts, wobble = [], 0.0
    for x in range(0, width + step, step):
        t = x / float(width)
        smooth = sum(a * math.sin(f * math.pi * t + p) for a, f, p in comps)
        peaky = sum(a * (1 - abs(math.sin(f * math.pi * t + p))) for a, f, p in comps)
        wobble = wobble * 0.6 + rng.uniform(-1, 1) * jitter
        y = (1 - sharp) * smooth * 0.5 - sharp * peaky * 0.8 + wobble
        pts.append((x, base + y * amp))
    return pts


def _layer_from_points(img, pts, top_color, bottom_color, top_y, fade):
    """按一条轮廓线把一层山叠到 img 上：轮廓线以下填色，颜色从 top_y 处的 top_color
    在 fade 像素内过渡到 bottom_color（墨色山头向下化进雾里就是这一招）。pts 为 2 倍超采样坐标。"""
    w, h = img.size
    mask = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(mask).polygon(pts + [(w * 2, h * 2), (0, h * 2)], fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    column = Image.new("RGB", (1, h))
    for y in range(h):
        column.putpixel((0, y), tuple(int(c) for c in _mix(top_color, bottom_color, _smooth((y - top_y) / float(max(1, fade))))))
    img.paste(column.resize((w, h)), (0, 0), mask)


def _disc(img, cx, cy, r, color, glow=0.0, clip_below=None):
    """一个柔边圆盘（月亮 / 太阳），可带光晕；clip_below 给了就只画这条水平线以上的部分（落日沉进海里）。"""
    w, h = img.size
    mask = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(mask).ellipse(((cx - r) * 2, (cy - r) * 2, (cx + r) * 2, (cy + r) * 2), fill=255)
    mask = mask.resize((w, h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(0.8, r / 40.0)))
    if glow:
        halo = Image.new("L", (w, h), 0)
        gr = r * 3.2
        ImageDraw.Draw(halo).ellipse((cx - gr, cy - gr, cx + gr, cy + gr), fill=int(255 * glow))
        mask = ImageChops.lighter(mask, halo.filter(ImageFilter.GaussianBlur(r * 1.6)))
    if clip_below is not None:
        ImageDraw.Draw(mask).rectangle((0, int(clip_below), w, h), fill=0)
    img.paste(Image.new("RGB", (w, h), color), (0, 0), mask)


def _stars(img, rng, count, where, color=(236, 242, 255), sizes=(0.6, 0.8, 1.0, 1.0, 1.3, 1.8), bright=(60, 230)):
    """where(x, y) -> [0, 1] 的接受概率：星星在银河带里更密、贴近地平线变淡都靠它。"""
    w, h = img.size
    layer = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(layer)
    scale = max(w, h) / 2560.0
    placed, tries = 0, 0
    while placed < count and tries < count * 12:
        tries += 1
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        p = where(x, y)
        if p <= 0 or rng.random() > p:
            continue
        rad = rng.choice(sizes) * scale
        draw.ellipse((x - rad, y - rad, x + rad, y + rad), fill=int(rng.uniform(*bright) * min(1.0, 0.35 + p)))
        placed += 1
    img.paste(Image.new("RGB", img.size, color), (0, 0), layer.filter(ImageFilter.GaussianBlur(0.6 * scale)))


def inkhill(size, seed=41):
    """墨山：月夜里的水墨远山——山头着墨、山脚化进雾里，一层比一层近、一层比一层深，右上一轮淡月。"""
    rng = random.Random(seed)
    w, h = size
    portrait = h > w
    sky = [(0.0, (12, 14, 18)), (0.45, (26, 30, 36)), (0.8, (50, 54, 58)), (1.0, (62, 64, 66))]
    moon = (0.72 if not portrait else 0.68, 0.19 if not portrait else 0.15)

    def shader(u, v, aspect):
        r, g, b = _gradient(sky, v)
        k = 0.28 * _blob(u, v, aspect, moon[0], moon[1], 0.24 * aspect, 0.22)
        return r + 150 * k, g + 146 * k, b + 136 * k

    img = _field(size, shader)
    _disc(img, moon[0] * w, moon[1] * h, 0.040 * min(w, h) * (1.25 if portrait else 1.0), (226, 220, 204), glow=0.10)
    # (山脚高度, 起伏, 山头墨色, 化进的雾色, 化开的距离)。远山淡、近山浓；雾色比天色略亮，层与层之间就隔着一道白雾。
    layers = [(0.50, 0.20, (64, 68, 74), (92, 96, 100), 0.13), (0.60, 0.18, (46, 50, 56), (84, 88, 92), 0.12),
              (0.70, 0.16, (30, 33, 38), (70, 74, 78), 0.11), (0.81, 0.13, (16, 18, 22), (52, 55, 58), 0.10),
              (0.93, 0.08, (6, 7, 9), (10, 11, 13), 0.20)]
    for i, (base, amp, ink, mist, fade) in enumerate(layers):
        pts = _peak_points(rng, w * 2, base * h * 2, amp * h * 2, 8, sharp=0.72 if i < 3 else 0.45, waves=4, jitter=0.007)
        top = sum(p[1] for p in pts) / len(pts) / 2.0 - amp * h * 0.25
        _layer_from_points(img, pts, ink, mist, top, fade * h)
    return _grain(img, 0.13, seed)


def galaxy(size, seed=53):
    """星河：一条斜贯夜空的银河（暖白的核、偏蓝的边、几道暗尘带），底下一抹近黑的山丘和一线地平线辉光。"""
    rng = random.Random(seed)
    w, h = size
    aspect0 = w / float(h)
    ang = math.radians(-24 if w > h else -64)
    cx, cy = 0.50 * aspect0, 0.44
    sin_a, cos_a = math.sin(ang), math.cos(ang)
    width = 0.20 if w > h else 0.16
    bulge = (0.34 * aspect0, 0.62) if w > h else (0.40 * aspect0, 0.64)
    dust = _fbm_layers(seed, 6, 4)
    cloud = _fbm_layers(seed + 17, 4, 5)
    sky = [(0.0, (3, 5, 12)), (0.6, (6, 10, 22)), (0.86, (12, 18, 30)), (1.0, (30, 34, 36))]

    def band_at(X, Y):
        d = (X - cx) * sin_a - (Y - cy) * cos_a
        return d, math.exp(-(d / width) ** 2)

    def shader(u, v, aspect):
        r, g, b = _gradient(sky, v)
        X = u * aspect
        d, band = band_at(X, v)
        n = _fbm(cloud, u, v)
        glow = band * max(0.0, n - 0.22) ** 1.3 * 2.2
        # 暗尘带：沿带子方向拉长的噪声阈值，偏在核心一侧
        lane = _smooth((_fbm(dust, u, v) - 0.48) / 0.12) * math.exp(-((d + 0.02) / (width * 0.45)) ** 2)
        glow *= 1 - 0.85 * lane
        core = math.exp(-(d / (width * 0.4)) ** 2)
        warm = math.exp(-(((X - bulge[0]) / 0.30) ** 2 + ((v - bulge[1]) / 0.22) ** 2)) * band
        r += glow * (44 + 60 * core) + warm * 70
        g += glow * (52 + 50 * core) + warm * 50
        b += glow * (82 + 30 * core) + warm * 30
        # 地平线辉光：一线极淡的暖灰
        k = math.exp(-((v - 0.93) / 0.07) ** 2)
        return r + 18 * k, g + 18 * k, b + 14 * k

    img = _field(size, shader)

    def where(x, y):
        _, band = band_at(x / float(h), y / float(h))
        fade = 1 - _smooth((y / float(h) - 0.74) / 0.18)
        return (0.14 + 0.86 * band) * fade

    _stars(img, rng, int(w * h / 1100), where, sizes=(0.5, 0.6, 0.7, 0.8, 1.0), bright=(40, 170))
    _stars(img, rng, int(w * h / 5200), where)
    _stars(img, rng, int(w * h / 80000), where, color=(255, 236, 214), sizes=(1.6, 2.0, 2.4), bright=(150, 240))
    pts = _ridge_points(rng, w * 2, h * 0.91 * 2, h * 0.05 * 2, 0.10, 8)
    _layer_from_points(img, pts, (6, 8, 14), (3, 4, 8), min(p[1] for p in pts) / 2.0, h * 0.1)
    return _grain(img, 0.08, seed)


def seabay(size, seed=67):
    """海湾：落日刚碰到海平线，天空从夜蓝过渡到橘色，海面一道碎金似的倒影，左侧一段深色岬角。"""
    rng = random.Random(seed)
    w, h = size
    portrait = h > w
    horizon = 0.60 if not portrait else 0.56
    sun_u = 0.64 if not portrait else 0.58
    sky = [(0.0, (12, 20, 42)), (0.45, (38, 52, 86)), (0.75, (150, 104, 104)), (0.92, (222, 142, 92)), (1.0, (238, 170, 110))]
    sea = [(0.0, (70, 64, 82)), (0.18, (34, 42, 66)), (1.0, (6, 12, 24))]

    def shader(u, v, aspect):
        if v < horizon:
            r, g, b = _gradient(sky, v / horizon)
            k = 0.55 * _blob(u, v, aspect, sun_u, horizon, 0.34 * aspect, 0.16)
            return r + 90 * k, g + 52 * k, b + 18 * k
        r, g, b = _gradient(sea, (v - horizon) / (1 - horizon))
        k = 0.50 * _blob(u, v, aspect, sun_u, horizon + 0.02, 0.10 * aspect, 0.30)
        return r + 150 * k, g + 86 * k, b + 36 * k

    img = _field(size, shader)
    hy = horizon * h
    _disc(img, sun_u * w, hy + 0.004 * h, 0.034 * min(w, h) * (1.3 if portrait else 1.0), (255, 214, 160), glow=0.22, clip_below=hy)
    # 倒影：海平线往下，一条条横向的短光，越远越窄越密、越近越宽越稀。
    glints = Image.new("L", size, 0)
    draw = ImageDraw.Draw(glints)
    for _ in range(int(900 * (w * h) / (2560 * 1440.0))):
        t = rng.random() ** 1.8
        y = hy + 2 + t * (h - hy) * 0.9
        spread = (0.02 + 0.10 * t) * w
        x = sun_u * w + rng.gauss(0, 1) * spread * 0.55
        length = rng.uniform(0.2, 1.0) * (6 + 60 * t) * max(w, h) / 2560.0
        alpha = int(rng.uniform(50, 200) * (1 - 0.7 * t) * math.exp(-((x - sun_u * w) / (spread * 1.3)) ** 2))
        draw.line((x - length, y, x + length, y), fill=alpha, width=max(1, int(1 + 2.5 * t * max(w, h) / 2560.0)))
    img.paste(Image.new("RGB", size, (255, 196, 128)), (0, 0), glints.filter(ImageFilter.GaussianBlur(0.8)))
    # 海面细纹：满幅很淡的横线，只为让大块暗色不显得像纯色块。
    ripples = Image.new("L", size, 0)
    draw = ImageDraw.Draw(ripples)
    for _ in range(int(1400 * (w * h) / (2560 * 1440.0))):
        t = rng.random()
        y = hy + 3 + t * (h - hy)
        x = rng.uniform(0, w)
        length = (8 + 90 * t) * max(w, h) / 2560.0
        draw.line((x - length, y, x + length, y), fill=int(rng.uniform(10, 34)), width=1)
    img.paste(Image.new("RGB", size, (150, 170, 210)), (0, 0), ripples.filter(ImageFilter.GaussianBlur(0.7)))
    # 岬角：从左边伸进海里的一段深色陆地，脚下压着海平线。
    land = Image.new("L", (w * 2, h * 2), 0)
    end = (0.36 if not portrait else 0.50) * w * 2
    pts = []
    for i in range(0, 41):
        t = i / 40.0
        x = t * end
        rise = (1 - _smooth(t)) * 0.075 * h + 0.012 * h * math.sin(t * 9 + 1.3) * (1 - t)
        pts.append((x, (hy - rise - rng.uniform(0, 0.003) * h) * 2))
    ImageDraw.Draw(land).polygon([(0, hy * 2 + 6)] + pts + [(end, hy * 2 + 6)], fill=255)
    img.paste(Image.new("RGB", size, (14, 16, 28)), (0, 0), land.resize(size, Image.LANCZOS))
    return _grain(img, 0.08, seed)


def flow(size, seed=97):
    """流光：几条丝带似的深青色光带在墨色底上缓缓弯过，右下角压一抹琥珀，没有具体景物，只给首页一层有质感的底色。"""
    warp = _fbm_layers(seed, 2, 4)
    palette = [(0.0, (5, 9, 14)), (0.30, (8, 26, 34)), (0.55, (14, 58, 66)), (0.72, (34, 96, 100)), (0.82, (14, 58, 66)), (1.0, (5, 9, 14))]

    def shader(u, v, aspect):
        X = u * aspect
        n = _fbm(warp, u, v)
        phase = v * 1.3 - X * 0.35 + 0.22 * math.sin(X * 2.2 + 0.6) + 0.55 * (n - 0.5)
        t = (math.sin(phase * math.tau * 1.15) * 0.5 + 0.5) ** 1.6
        r, g, b = _gradient(palette, t)
        vignette = 1 - 0.45 * _smooth(math.hypot(u - 0.5, (v - 0.45) * 1.1) / 0.75)
        k = 0.55 * _blob(u, v, aspect, 0.86, 0.86, 0.36 * aspect, 0.30)
        return r * vignette + 96 * k, g * vignette + 54 * k, b * vignette + 22 * k

    return _grain(_field(size, shader), 0.10, seed)


WALLPAPERS = [("aurora", "极光", aurora), ("dusk", "暮色群山", dusk), ("mist", "晨雾山林", mist),
              ("inkhill", "月夜墨山", inkhill), ("galaxy", "星河", galaxy), ("seabay", "海湾落日", seabay),
              ("flow", "流光", flow)]


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


def main(argv=None):
    """不带参数：全部重画。带壁纸 id：只重画这几张，manifest.json 里其它条目原样保留（重画会因 Pillow 版本
    不同得到字节不同的文件，没改的就别动）。--out <目录> 输出到别处（预览用）。"""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    out = OUT_DIR
    if "--out" in args:
        at = args.index("--out")
        out = Path(args[at + 1])
        del args[at:at + 2]
    known = [wid for wid, _, _ in WALLPAPERS]
    unknown = [a for a in args if a not in known]
    if unknown:
        raise SystemExit("不认识的壁纸：%s（可选：%s）" % (", ".join(unknown), ", ".join(known)))
    only = set(args) or set(known)
    out.mkdir(parents=True, exist_ok=True)
    try:
        old = {e["id"]: e for e in json.loads((out / "manifest.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        old = {}
    manifest = []
    for wid, name, painter in WALLPAPERS:
        if wid not in only:
            if wid in old:
                manifest.append(old[wid])
            continue
        desktop, mobile = painter(DESKTOP), painter(MOBILE)
        info = stats(desktop)
        # 竖版构图不同，亮度取两者中更亮的那个，遮罩按最坏情况算。
        info["lum"] = max(info["lum"], stats(mobile)["lum"])
        sizes = {
            "desktop": _save(desktop, out / (wid + ".webp"), 82, 320 * 1024),
            "mobile": _save(mobile, out / (wid + "-m.webp"), 80, 200 * 1024),
            "thumb": _save(desktop.resize(THUMB, Image.LANCZOS), out / (wid + "-thumb.webp"), 78, 12 * 1024),
        }
        manifest.append({"id": wid, "name": name, "color": info["color"], "lum": info["lum"]})
        print("%-7s color=%s lum=%.3f  %s" % (wid, info["color"], info["lum"],
                                                "  ".join("%s=%dKB" % (k, v // 1024) for k, v in sizes.items())))
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
