"""7. Motion Graphics & HUD Engine — couche graphique animée.

Un vrai compositeur image par image : chaque élément est dessiné pour l'instant
t (courbes d'easing de motion.py), puis la couche transparente est encodée
(QuickTime Animation, avec canal alpha) et superposée à la vidéo par FFmpeg.

Éléments disponibles (type dans la chronologie) :

    hook            accroche en haut d'écran, mot à mot, mot-clé surligné
    word            texte cinétique : pop / slam / slide, rotation, contour, halo
    notify          notification façon kill feed (carte qui glisse, icône)
    headshot        tampon « HEADSHOT » avec viseur qui s'élargit
    combo           compteur de combo qui rebondit à chaque coup
    victory         bandeau de victoire avec trophée et étincelles
    title           titre animé (introduction du format long)
    lower_third     bandeau bas : nom de chaîne / jeu
    scoreboard      numéro du moment et jauge d'intensité
    hud             coins de visée, REC, chrono, compteur de clips, pulsation sur la musique
    progress        barre de progression (format court)
    sparks          gerbe d'étincelles (particules avec gravité et traînée)
    shockwave       onde de choc circulaire
    explosion       explosion complète : lueur, onde de choc, étincelles, débris
    embers          braises qui flottent (ambiance)
    subscribe       fin de vidéo : bouton « S'ABONNER » qui devient « ABONNÉ »

Icônes vectorielles dessinées par le programme : viseur, crâne, flamme,
trophée, éclair, étoile, cloche.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..ffutil import _popen_kwargs, ffmpeg_bin
from .motion import ease_in_cubic, ease_out_back, ease_out_cubic, punch_envelope

# ---------------------------------------------------------------------------
# Polices
# ---------------------------------------------------------------------------

_WIN_FONTS = {
    "Impact": "impact.ttf", "Arial Black": "ariblk.ttf", "Bahnschrift": "bahnschrift.ttf",
    "Segoe UI Black": "seguibl.ttf", "Arial Bold": "arialbd.ttf",
}
DISPLAY_FONTS = ["Impact", "Bahnschrift", "Arial Black", "Segoe UI Black",
                 "Inter Display Black", "Anton", "Bebas Neue", "DejaVu Sans Bold"]


@lru_cache(maxsize=64)
def font_path(name: str) -> str | None:
    if sys.platform.startswith("win"):
        file = Path("C:/Windows/Fonts") / _WIN_FONTS.get(name, "")
        return str(file) if file.is_file() else None
    if shutil.which("fc-match"):
        try:
            family, _, style = name.partition(" Bold") if name.endswith("Bold") else (name, "", "")
            query = f"{family}:bold" if name.endswith("Bold") else name
            out = subprocess.run(["fc-match", "-f", "%{file}|%{family}", query],
                                 capture_output=True, text=True, timeout=10).stdout
            file, _, fam = out.partition("|")
            if file and name.split()[0].lower() in fam.lower():
                return file
        except (OSError, subprocess.TimeoutExpired):
            return None
    return None


def available_fonts() -> list[str]:
    return [f for f in DISPLAY_FONTS if font_path(f)]


@lru_cache(maxsize=256)
def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = font_path(name)
    for candidate in [path] + [font_path(f) for f in DISPLAY_FONTS]:
        if candidate:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


# ---------------------------------------------------------------------------
# Contexte et utilitaires de dessin
# ---------------------------------------------------------------------------


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@dataclass
class Context:
    width: int
    height: int
    fps: float
    duration: float
    palette: dict = field(default_factory=lambda: {
        "accent": "#B6F24A", "accent2": "#5AD1FF", "text": "#FFFFFF", "shadow": "#000000"})
    font: str = "Impact"
    beats: list = field(default_factory=list)
    clips: list = field(default_factory=list)  # instants de début des clips (temps monté)

    @property
    def s(self) -> float:
        """Facteur d'échelle : 1.0 pour 1080 px sur le petit côté."""
        return min(self.width, self.height) / 1080.0

    @property
    def vertical(self) -> bool:
        return self.height > self.width

    def color(self, key: str, alpha: int = 255) -> tuple[int, int, int, int]:
        return (*hex_rgb(self.palette.get(key, "#FFFFFF")), alpha)

    def beat_pulse(self, t: float) -> float:
        """0..1 : impulsion sur chaque temps de la musique."""
        best = 0.0
        for b in self.beats:
            if b > t:
                break
            best = max(best, punch_envelope(t - b, attack=0.02, decay=8))
        return min(1.0, best)


@lru_cache(maxsize=512)
def text_sprite(text: str, font: str, size: int, fill: tuple, stroke: int,
                stroke_fill: tuple, glow: tuple | None = None, glow_radius: int = 0,
                shadow: int = 0) -> Image.Image:
    """Mot pré-rendu (contour, ombre portée, halo) : réutilisé à chaque image."""
    f = load_font(font, size)
    box = f.getbbox(text, stroke_width=stroke)
    pad = stroke + glow_radius * 2 + shadow + 4
    w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    origin = (pad - box[0], pad - box[1])
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((origin[0] + shadow, origin[1] + shadow), text, font=f,
                                fill=(0, 0, 0, 170), stroke_width=stroke,
                                stroke_fill=(0, 0, 0, 170))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(max(1, shadow // 2))))
    if glow and glow_radius:
        gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(gl).text(origin, text, font=f, fill=glow, stroke_width=stroke + 2,
                                stroke_fill=glow)
        gl = gl.filter(ImageFilter.GaussianBlur(glow_radius))
        img = Image.alpha_composite(img, gl)
        img = Image.alpha_composite(img, gl)
    top = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(top).text(origin, text, font=f, fill=fill, stroke_width=stroke,
                             stroke_fill=stroke_fill)
    return Image.alpha_composite(img, top)


def paste(canvas: Image.Image, sprite: Image.Image, cx: float, cy: float, scale: float = 1.0,
          angle: float = 0.0, alpha: float = 1.0) -> None:
    """Colle un sprite centré en (cx, cy), avec échelle, rotation et opacité."""
    if scale <= 0.01 or alpha <= 0.01:
        return
    img = sprite
    if abs(scale - 1.0) > 0.01:
        w, h = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        img = img.resize((w, h), Image.BILINEAR)
    if abs(angle) > 0.2:
        img = img.rotate(angle, resample=Image.BILINEAR, expand=True)
    if alpha < 0.99:
        a = img.getchannel("A").point(lambda v: int(v * alpha))
        img = img.copy()
        img.putalpha(a)
    canvas.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2))) \
        if _inside(canvas, img, cx, cy) else _paste_clipped(canvas, img, cx, cy)


def _inside(canvas: Image.Image, img: Image.Image, cx: float, cy: float) -> bool:
    x, y = int(cx - img.width / 2), int(cy - img.height / 2)
    return x >= 0 and y >= 0 and x + img.width <= canvas.width and y + img.height <= canvas.height


def _paste_clipped(canvas: Image.Image, img: Image.Image, cx: float, cy: float) -> None:
    x, y = int(cx - img.width / 2), int(cy - img.height / 2)
    left, top = max(0, -x), max(0, -y)
    right = min(img.width, canvas.width - x)
    bottom = min(img.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(img.crop((left, top, right, bottom)), (x + left, y + top))


# ---------------------------------------------------------------------------
# Icônes vectorielles
# ---------------------------------------------------------------------------


@lru_cache(maxsize=128)
def icon(name: str, size: int, color: tuple, accent: tuple) -> Image.Image:
    k = 4  # sur-échantillonnage pour des bords lisses
    S = size * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, w = S / 2, max(2, S // 14)
    if name == "crosshair":
        r = S * 0.36
        d.ellipse((c - r, c - r, c + r, c + r), outline=color, width=w)
        for a in range(4):
            dx, dy = math.cos(a * math.pi / 2), math.sin(a * math.pi / 2)
            d.line((c + dx * r * 0.45, c + dy * r * 0.45, c + dx * r * 1.3, c + dy * r * 1.3),
                   fill=color, width=w)
        d.ellipse((c - w, c - w, c + w, c + w), fill=accent)
    elif name == "skull":
        d.ellipse((S * 0.2, S * 0.12, S * 0.8, S * 0.68), fill=color)
        d.rounded_rectangle((S * 0.32, S * 0.55, S * 0.68, S * 0.84), radius=S * 0.05, fill=color)
        for ex in (0.38, 0.62):
            d.ellipse((S * (ex - 0.09), S * 0.36, S * (ex + 0.09), S * 0.53), fill=(0, 0, 0, 0))
        d.polygon([(S * 0.5, S * 0.55), (S * 0.46, S * 0.63), (S * 0.54, S * 0.63)],
                  fill=(0, 0, 0, 0))
        for tx in (0.42, 0.5, 0.58):
            d.line((S * tx, S * 0.72, S * tx, S * 0.84), fill=(0, 0, 0, 0), width=w // 2)
    elif name == "flame":
        pts = [(0.5, 0.05), (0.72, 0.38), (0.66, 0.24), (0.85, 0.55), (0.78, 0.82), (0.5, 0.95),
               (0.22, 0.82), (0.15, 0.55), (0.32, 0.3), (0.36, 0.45)]
        d.polygon([(x * S, y * S) for x, y in pts], fill=accent)
        inner = [(0.5, 0.4), (0.64, 0.66), (0.5, 0.88), (0.36, 0.66)]
        d.polygon([(x * S, y * S) for x, y in inner], fill=color)
    elif name == "trophy":
        d.pieslice((S * 0.22, S * 0.0, S * 0.78, S * 0.62), 0, 180, fill=accent)
        d.rectangle((S * 0.22, S * 0.1, S * 0.78, S * 0.31), fill=accent)
        for side in (0.14, 0.86):
            d.arc((S * (side - 0.1), S * 0.14, S * (side + 0.1), S * 0.4), 0, 360, fill=accent,
                  width=w)
        d.rectangle((S * 0.45, S * 0.6, S * 0.55, S * 0.78), fill=accent)
        d.rounded_rectangle((S * 0.28, S * 0.78, S * 0.72, S * 0.9), radius=S * 0.03, fill=color)
    elif name == "bolt":
        pts = [(0.58, 0.04), (0.2, 0.55), (0.46, 0.55), (0.38, 0.96), (0.8, 0.4), (0.53, 0.4)]
        d.polygon([(x * S, y * S) for x, y in pts], fill=accent)
    elif name == "star":
        pts = []
        for i in range(10):
            r = S * (0.46 if i % 2 == 0 else 0.2)
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((c + r * math.cos(a), c + r * math.sin(a)))
        d.polygon(pts, fill=accent)
    elif name == "bell":
        d.pieslice((S * 0.2, S * 0.12, S * 0.8, S * 0.8), 180, 360, fill=color)
        d.rectangle((S * 0.2, S * 0.45, S * 0.8, S * 0.72), fill=color)
        d.polygon([(S * 0.12, S * 0.78), (S * 0.88, S * 0.78), (S * 0.8, S * 0.68),
                   (S * 0.2, S * 0.68)], fill=color)
        d.ellipse((S * 0.42, S * 0.78, S * 0.58, S * 0.94), fill=accent)
    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Éléments
# ---------------------------------------------------------------------------


def _life(t: float, start: float, end: float, fade_in: float, fade_out: float) -> tuple[float, float]:
    """(progression d'entrée 0..1, progression de sortie 0..1)."""
    return (min(1.0, max(0.0, (t - start) / max(fade_in, 1e-3))),
            min(1.0, max(0.0, (t - (end - fade_out)) / max(fade_out, 1e-3))))


class Element:
    layer = 5

    def __init__(self, item: dict, ctx: Context):
        self.item = item
        self.ctx = ctx
        self.start = float(item["start"])
        self.end = float(item.get("end", self.start + 1.0))

    def active(self, t: float) -> bool:
        return self.start <= t <= self.end

    def draw(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, t: float) -> None:
        raise NotImplementedError


class Word(Element):
    """Texte cinétique."""
    layer = 7

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        size = int(it.get("size", 120) * c.s)
        color = c.color(it.get("color", "accent"))
        sprite = text_sprite(it["text"], c.font, size, color, max(3, size // 12),
                             (0, 0, 0, 255), glow=c.color(it.get("color", "accent"), 150),
                             glow_radius=max(4, size // 10), shadow=max(2, size // 18))
        style = it.get("style", "pop")
        x, y = it.get("pos", [0.5, 0.62])
        cx, cy = x * c.width, y * c.height
        enter, leave = _life(t, self.start, self.end, 0.22, 0.18)
        angle = float(it.get("angle", 0))
        if style == "slam":
            scale = 2.2 - 1.2 * ease_out_cubic(enter)
            alpha = min(1.0, enter * 3)
        elif style == "slide":
            scale = 1.0
            cx += (1 - ease_out_cubic(enter)) * c.width * 0.6 * (-1 if angle < 0 else 1)
            alpha = 1.0
        else:  # pop
            scale = 0.3 + 0.7 * ease_out_back(enter, 2.2)
            alpha = min(1.0, enter * 4)
        scale *= 1.0 - 0.25 * ease_in_cubic(leave)
        alpha *= 1.0 - leave
        scale *= 1.0 + 0.04 * c.beat_pulse(t)
        paste(canvas, sprite, cx, cy, scale, angle, alpha)


class Hook(Element):
    """Accroche : mots qui apparaissent un à un, mot-clé sur fond coloré."""
    layer = 8

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        words = it["text"].upper().split()
        size = int((92 if c.vertical else 80) * c.s)
        key = max(range(len(words)), key=lambda i: len(words[i])) if words else 0
        sprites = []
        for i, w in enumerate(words):
            fill = (0, 0, 0, 255) if i == key else c.color("text")
            sprites.append(text_sprite(w, c.font, size, fill, 0 if i == key else max(3, size // 12),
                                       (0, 0, 0, 255), shadow=0 if i == key else max(2, size // 16)))
        max_w = c.width * 0.86
        lines, line, lw = [], [], 0.0
        for i, sp in enumerate(sprites):
            if line and lw + sp.width > max_w:
                lines.append(line)
                line, lw = [], 0.0
            line.append(i)
            lw += sp.width + size * 0.15
        if line:
            lines.append(line)
        top = c.height * (0.14 if c.vertical else 0.1)
        _, leave = _life(t, self.start, self.end, 0.1, 0.25)
        for li, idxs in enumerate(lines):
            total = sum(sprites[i].width for i in idxs) + size * 0.15 * (len(idxs) - 1)
            x = (c.width - total) / 2
            y = top + li * size * 1.15
            for i in idxs:
                sp = sprites[i]
                appear = self.start + 0.09 * i
                p = min(1.0, max(0.0, (t - appear) / 0.25))
                if p <= 0:
                    x += sp.width + size * 0.15
                    continue
                cx, cy = x + sp.width / 2, y + size * 0.62  # même ligne de base pour tous les mots
                if i == key:  # bandeau coloré qui s'étire derrière le mot-clé
                    bw = sp.width * ease_out_cubic(min(1.0, p * 1.4)) + size * 0.2
                    a = int(255 * (1 - leave))
                    draw.rounded_rectangle((cx - bw / 2, cy - sp.height * 0.42,
                                            cx + bw / 2, cy + sp.height * 0.42),
                                           radius=size * 0.12, fill=c.color("accent", a))
                scale = (0.4 + 0.6 * ease_out_back(p, 2.5)) * (1 - 0.15 * leave)
                paste(canvas, sp, cx, cy - (1 - ease_out_cubic(p)) * size * 0.4, scale, 0,
                      (1 - leave) * min(1.0, p * 3))
                x += sp.width + size * 0.15


class Notify(Element):
    """Notification façon kill feed."""
    layer = 9

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        s = c.s
        size = int(40 * s)
        sprite = text_sprite(it["text"].upper(), c.font, size, c.color("text"), 0, (0, 0, 0, 0))
        ic = icon(it.get("icon", "crosshair"), int(size * 1.2), c.color("text"), c.color("accent"))
        w = sprite.width + ic.width + size * 1.2
        h = size * 1.9
        slot = int(it.get("slot", 0))
        margin = 32 * s
        y = (c.height * (0.34 if c.vertical else 0.08)) + slot * (h + 12 * s)
        enter, leave = _life(t, self.start, self.end, 0.28, 0.25)
        x_end = c.width - margin - w
        x = x_end + (1 - ease_out_back(enter, 1.4)) * (w + margin * 2) + ease_in_cubic(leave) * (w + margin * 2)
        alpha = int(235 * (1 - leave))
        card = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, w - 1, h - 1), radius=h * 0.18, fill=(12, 14, 20, int(alpha * 0.85)))
        cd.rectangle((0, h * 0.12, max(4, 7 * s), h * 0.88), fill=c.color("accent", alpha))
        flash = max(0.0, 1 - (t - self.start) / 0.35)
        if flash > 0:
            cd.rounded_rectangle((0, 0, w - 1, h - 1), radius=h * 0.18,
                                 outline=c.color("accent", int(255 * flash)), width=max(2, int(4 * s)))
        card.alpha_composite(ic, (int(size * 0.5), int((h - ic.height) / 2)))
        card.alpha_composite(sprite, (int(size * 0.8 + ic.width), int((h - sprite.height) / 2)))
        if leave > 0:
            card.putalpha(card.getchannel("A").point(lambda v: int(v * (1 - leave))))
        _paste_clipped(canvas, card, x + w / 2, y + h / 2)


class Headshot(Element):
    layer = 9

    def draw(self, canvas, draw, t):
        c = self.ctx
        dt = t - self.start
        size = int(150 * c.s)
        cx, cy = c.width / 2, c.height * (0.42 if c.vertical else 0.4)
        ring = icon("crosshair", int(size * 1.8), c.color("accent"), c.color("text"))
        grow = ease_out_cubic(min(1.0, dt / 0.35))
        _, leave = _life(t, self.start, self.end, 0.1, 0.25)
        paste(canvas, ring, cx, cy, 0.5 + 0.9 * grow, dt * 40, (1 - grow * 0.6) * (1 - leave))
        sprite = text_sprite(self.item.get("text", "HEADSHOT"), c.font, size, c.color("text"),
                             max(4, size // 11), (0, 0, 0, 255), glow=c.color("accent", 180),
                             glow_radius=max(6, size // 8), shadow=4)
        scale = 2.4 - 1.4 * ease_out_cubic(min(1.0, dt / 0.18))
        shake = 10 * c.s * punch_envelope(dt - 0.18, 0.02, 9)
        paste(canvas, sprite, cx + shake * math.sin(dt * 80), cy, scale * (1 - 0.2 * leave), -7,
              min(1.0, dt * 6) * (1 - leave))


class Combo(Element):
    layer = 8

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        hits = [h for h in it.get("hits", []) if h <= t]
        if not hits:
            return
        n = len(hits)
        size = int(70 * c.s)
        enter, leave = _life(t, self.start, self.end, 0.2, 0.3)
        bump = punch_envelope(t - hits[-1], 0.03, 7)
        label = text_sprite("COMBO", c.font, int(size * 0.55), c.color("text"), 3, (0, 0, 0, 255),
                            shadow=3)
        num = text_sprite(f"x{n}", c.font, int(size * 1.3), c.color("accent"),
                          max(4, size // 10), (0, 0, 0, 255), glow=c.color("accent", 160),
                          glow_radius=8, shadow=4)
        x = c.width * (0.82 if c.vertical else 0.9)
        y = c.height * (0.5 if c.vertical else 0.45)
        slide = (1 - ease_out_cubic(enter)) * c.width * 0.3
        alpha = 1 - leave
        paste(canvas, label, x + slide, y - size * 0.75, 1, -6, alpha)
        paste(canvas, num, x + slide, y + size * 0.15, 1 + 0.45 * bump, -6, alpha)
        fl = icon("flame", int(size * 0.9), c.color("text"), c.color("accent2"))
        paste(canvas, fl, x + slide - num.width * 0.62, y + size * 0.1, 1 + 0.3 * bump, 0, alpha)


class Victory(Element):
    layer = 9

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        enter, leave = _life(t, self.start, self.end, 0.4, 0.35)
        cy = c.height * (0.45 if c.vertical else 0.5)
        band_h = 210 * c.s
        reveal = ease_out_cubic(enter)
        a = int(220 * (1 - leave))
        draw.polygon([(0, cy - band_h / 2), (c.width * reveal, cy - band_h / 2 - 30 * c.s),
                      (c.width * reveal, cy + band_h / 2 - 30 * c.s), (0, cy + band_h / 2)],
                     fill=c.color("accent", a))
        size = int(130 * c.s)
        sprite = text_sprite(it.get("text", "VICTOIRE"), c.font, size, (10, 10, 10, 255), 0,
                             (0, 0, 0, 0))
        p = min(1.0, max(0.0, (t - self.start - 0.2) / 0.3))
        paste(canvas, sprite, c.width / 2, cy - 12 * c.s, 0.6 + 0.4 * ease_out_back(p, 2),
              -1.5, p * (1 - leave))
        tr = icon("trophy", int(size * 1.1), c.color("text"), (255, 205, 60, 255))
        paste(canvas, tr, c.width / 2, cy - band_h * 1.05, ease_out_back(p, 2.5), 0, p * (1 - leave))
        rng = np.random.default_rng(int(self.start * 1000))
        for _ in range(14):  # étincelles autour du trophée
            ang, rad = rng.uniform(0, 2 * math.pi), rng.uniform(0.6, 1.4) * size
            tw = 0.5 + 0.5 * math.sin(t * 12 + ang * 3)
            st = icon("star", int(size * 0.25 * tw + 4), c.color("text"), (255, 230, 120, 255))
            paste(canvas, st, c.width / 2 + math.cos(ang) * rad,
                  cy - band_h * 1.05 + math.sin(ang) * rad * 0.6, p, 0, p * (1 - leave) * tw)


class Title(Element):
    layer = 8

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        enter, leave = _life(t, self.start, self.end, 0.6, 0.4)
        size = int(110 * c.s)
        cx, cy = c.width / 2, c.height * 0.45
        line_w = c.width * 0.5 * ease_out_cubic(enter)
        a = int(255 * (1 - leave))
        draw.rectangle((cx - line_w / 2, cy + size * 0.7, cx + line_w / 2, cy + size * 0.7 + 6 * c.s),
                       fill=c.color("accent", a))
        sp = text_sprite(it["text"].upper(), c.font, size, c.color("text"), 5, (0, 0, 0, 255),
                         glow=c.color("accent", 120), glow_radius=10, shadow=5)
        paste(canvas, sp, cx, cy - (1 - ease_out_cubic(enter)) * 40 * c.s, 1, 0,
              min(1.0, enter * 2) * (1 - leave))
        if it.get("subtitle"):
            sub = text_sprite(it["subtitle"].upper(), c.font, int(size * 0.35), c.color("accent"),
                              2, (0, 0, 0, 255))
            p = min(1.0, max(0.0, (t - self.start - 0.35) / 0.4))
            paste(canvas, sub, cx, cy + size * 1.15, 1, 0, p * (1 - leave))


class LowerThird(Element):
    layer = 6

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        enter, leave = _life(t, self.start, self.end, 0.5, 0.4)
        s = c.s
        x0 = 48 * s
        y0 = c.height * (0.8 if c.vertical else 0.78)
        title = text_sprite(it["title"].upper(), c.font, int(54 * s), c.color("text"), 0, (0, 0, 0, 0))
        sub = text_sprite(it.get("subtitle", "").upper() or " ", c.font, int(30 * s),
                          (20, 20, 20, 255), 0, (0, 0, 0, 0))
        bar_w = (title.width + 60 * s) * ease_out_cubic(enter) * (1 - ease_in_cubic(leave))
        a = int(230 * (1 - leave))
        draw.rectangle((x0, y0, x0 + bar_w, y0 + title.height + 10 * s), fill=(10, 12, 18, a))
        draw.rectangle((x0, y0, x0 + 8 * s, y0 + title.height + 10 * s), fill=c.color("accent", a))
        p = min(1.0, max(0.0, (t - self.start - 0.25) / 0.35))
        if bar_w > title.width * 0.5:
            canvas.alpha_composite(_fade(title, p * (1 - leave)),
                                   (int(x0 + 30 * s), int(y0 + 5 * s + (1 - p) * 20 * s)))
        sub_w = (sub.width + 30 * s) * ease_out_cubic(min(1.0, max(0.0, (t - self.start - 0.4) / 0.4)))
        if sub_w > 2 and it.get("subtitle"):
            y1 = y0 + title.height + 10 * s
            draw.rectangle((x0, y1, x0 + sub_w * (1 - ease_in_cubic(leave)), y1 + sub.height + 8 * s),
                           fill=c.color("accent", a))
            if sub_w > sub.width * 0.6:
                canvas.alpha_composite(_fade(sub, (1 - leave)), (int(x0 + 15 * s), int(y1 + 4 * s)))


def _fade(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.99:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * max(0.0, alpha))))
    return out


class Scoreboard(Element):
    layer = 6

    def draw(self, canvas, draw, t):
        c, it = self.ctx, self.item
        enter, leave = _life(t, self.start, self.end, 0.35, 0.3)
        s = c.s
        x0 = 40 * s - (1 - ease_out_cubic(enter)) * 400 * s
        y0 = 40 * s
        a = int(220 * (1 - leave))
        txt = text_sprite(f"MOMENT {it['index']:02d}/{it['total']:02d}", c.font, int(38 * s),
                          c.color("text"), 0, (0, 0, 0, 0))
        w = txt.width + 40 * s
        draw.rounded_rectangle((x0, y0, x0 + w, y0 + 96 * s), radius=10 * s, fill=(10, 12, 18, a))
        canvas.alpha_composite(_fade(txt, 1 - leave), (int(x0 + 20 * s), int(y0 + 10 * s)))
        fill = float(it.get("score", 0.5)) * ease_out_cubic(min(1.0, (t - self.start) / 0.8))
        draw.rectangle((x0 + 20 * s, y0 + 66 * s, x0 + w - 20 * s, y0 + 78 * s), fill=(60, 64, 76, a))
        draw.rectangle((x0 + 20 * s, y0 + 66 * s, x0 + 20 * s + (w - 40 * s) * fill, y0 + 78 * s),
                       fill=c.color("accent", a))


class Hud(Element):
    layer = 3

    def draw(self, canvas, draw, t):
        c = self.ctx
        s = c.s
        intro = ease_out_cubic(min(1.0, (t - self.start) / 0.6))
        pulse = c.beat_pulse(t)
        m = 34 * s - 6 * s * pulse
        L = (70 + 18 * pulse) * s * intro
        a = int(200 * intro)
        col = c.color("text", a)
        wdt = max(2, int(5 * s))
        W, H = c.width, c.height
        for (x, y, dx, dy) in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
            draw.line((x, y, x + dx * L, y), fill=col, width=wdt)
            draw.line((x, y, x, y + dy * L), fill=col, width=wdt)
        # REC clignotant + chrono
        f = load_font(c.font, int(30 * s))
        if int(t * 2) % 2 == 0:
            draw.ellipse((m + 24 * s, m + 26 * s, m + 46 * s, m + 48 * s), fill=(255, 50, 50, a))
        draw.text((m + 58 * s, m + 20 * s), f"REC  {int(t // 60):02d}:{t % 60:05.2f}", font=f,
                  fill=col)
        if c.clips:
            idx = sum(1 for x in c.clips if x <= t)
            label = f"{idx:02d}/{len(c.clips):02d}"
            tw = f.getlength(label)
            draw.text((W - m - 24 * s - tw, m + 20 * s), label, font=f, fill=c.color("accent", a))


class Progress(Element):
    layer = 3

    def draw(self, canvas, draw, t):
        c = self.ctx
        s = c.s
        h = 9 * s
        y = c.height - h
        frac = min(1.0, max(0.0, t / max(c.duration, 0.1)))
        draw.rectangle((0, y, c.width, c.height), fill=(255, 255, 255, 60))
        draw.rectangle((0, y, c.width * frac, c.height), fill=c.color("accent", 255))
        g = c.beat_pulse(t)
        if g > 0.05:
            draw.ellipse((c.width * frac - 12 * s * (1 + g), y + h / 2 - 12 * s * (1 + g),
                          c.width * frac + 12 * s * (1 + g), y + h / 2 + 12 * s * (1 + g)),
                         fill=c.color("accent", int(160 * g)))


class Particles(Element):
    """Gerbe d'étincelles : position, vitesse, gravité, frottement, traînée lumineuse."""
    layer = 6

    def __init__(self, item, ctx):
        super().__init__(item, ctx)
        rng = np.random.default_rng(int(item.get("seed", 0)))
        n = int(item.get("count", 40))
        self.life = float(item.get("life", 0.7))
        self.end = self.start + self.life
        speed = rng.uniform(0.35, 1.0, n) * float(item.get("speed", 1.0)) * ctx.width * 1.6
        ang = rng.uniform(0, 2 * math.pi, n)
        if item.get("upward"):
            ang = rng.uniform(math.pi * 1.1, math.pi * 1.9, n)
        self.vx, self.vy = np.cos(ang) * speed, np.sin(ang) * speed
        self.lives = rng.uniform(0.5, 1.0, n) * self.life
        self.sizes = rng.uniform(0.6, 1.4, n)
        self.x0, self.y0 = item.get("x", 0.5) * ctx.width, item.get("y", 0.5) * ctx.height
        colors = [ctx.color("accent"), ctx.color("text"), (255, 190, 80, 255)]
        self.cols = [colors[int(i)] for i in rng.integers(0, 3, n)]

    def _pos(self, dt):
        drag = 3.2
        k = (1 - np.exp(-drag * dt)) / drag
        g = 1800 * self.ctx.s
        return self.x0 + self.vx * k, self.y0 + self.vy * k + 0.5 * g * dt * dt

    def draw(self, canvas, draw, t):
        dt = t - self.start
        if dt < 0:
            return
        x1, y1 = self._pos(dt)
        x0, y0 = self._pos(max(0.0, dt - 0.035))
        s = self.ctx.s
        for i in range(len(x1)):
            if dt > self.lives[i]:
                continue
            fade = 1 - dt / self.lives[i]
            col = self.cols[i][:3] + (int(255 * fade),)
            width = max(1, int(5 * s * self.sizes[i] * fade))
            draw.line((x0[i], y0[i], x1[i], y1[i]), fill=col, width=width)


class Shockwave(Element):
    layer = 5

    def __init__(self, item, ctx):
        super().__init__(item, ctx)
        self.end = self.start + float(item.get("life", 0.5))

    def draw(self, canvas, draw, t):
        c = self.ctx
        p = (t - self.start) / (self.end - self.start)
        r = ease_out_cubic(p) * c.width * float(self.item.get("radius", 0.55))
        x, y = self.item.get("x", 0.5) * c.width, self.item.get("y", 0.5) * c.height
        width = max(1, int(26 * c.s * (1 - p)))
        draw.ellipse((x - r, y - r, x + r, y + r), outline=c.color("text", int(200 * (1 - p))),
                     width=width)
        r2 = r * 0.82
        draw.ellipse((x - r2, y - r2, x + r2, y + r2),
                     outline=c.color("accent", int(150 * (1 - p))), width=max(1, width // 2))


@lru_cache(maxsize=16)
def radial_glow(size: int, color: tuple) -> Image.Image:
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (size / 2)
    a = np.clip(1 - d, 0, 1) ** 2.2
    img = np.zeros((size, size, 4), dtype=np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = color[0], color[1], color[2]
    img[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(img, "RGBA")


class Explosion(Element):
    layer = 6

    def __init__(self, item, ctx):
        super().__init__(item, ctx)
        self.end = self.start + 0.9
        self.parts = [
            Particles({**item, "count": 70, "life": 0.85, "speed": 1.2}, ctx),
            Particles({**item, "count": 25, "life": 0.6, "speed": 0.5,
                       "seed": int(item.get("seed", 0)) + 1}, ctx),
            Shockwave({**item, "life": 0.55, "radius": 0.6}, ctx),
        ]

    def draw(self, canvas, draw, t):
        c = self.ctx
        dt = t - self.start
        glow_p = punch_envelope(dt, 0.04, 4.5)
        if glow_p > 0.02:
            size = int(c.width * 0.9)
            g = radial_glow(min(size, 1400), (255, 170, 60))
            paste(canvas, g, self.item.get("x", 0.5) * c.width, self.item.get("y", 0.5) * c.height,
                  size / g.width * (0.7 + 0.5 * ease_out_cubic(min(1.0, dt / 0.3))), 0,
                  min(1.0, glow_p * 1.3))
        for p in self.parts:
            if p.active(t):
                p.draw(canvas, draw, t)


class Embers(Element):
    layer = 2

    def __init__(self, item, ctx):
        super().__init__(item, ctx)
        rng = np.random.default_rng(int(item.get("seed", 3)))
        n = 26
        self.x = rng.uniform(0, ctx.width, n)
        self.speed = rng.uniform(40, 110, n) * ctx.s
        self.phase = rng.uniform(0, 2 * math.pi, n)
        self.size = rng.uniform(2, 6, n) * ctx.s

    def draw(self, canvas, draw, t):
        c = self.ctx
        dt = t - self.start
        for i in range(len(self.x)):
            y = (c.height + 20 - (dt * self.speed[i] + self.phase[i] * 200) % (c.height + 40))
            x = self.x[i] + 25 * c.s * math.sin(dt * 1.3 + self.phase[i])
            a = int(140 * (0.5 + 0.5 * math.sin(dt * 3 + self.phase[i])))
            r = self.size[i]
            draw.ellipse((x - r, y - r, x + r, y + r), fill=c.color("accent2", a))


class Subscribe(Element):
    layer = 9

    def draw(self, canvas, draw, t):
        c = self.ctx
        s = c.s
        dt = t - self.start
        enter, leave = _life(t, self.start, self.end, 0.35, 0.3)
        clicked = dt > 0.9
        cx, cy = c.width / 2, c.height * (0.72 if c.vertical else 0.75)
        label = "ABONNÉ ✓" if clicked else "S'ABONNER"
        size = int(62 * s)
        fill = (30, 30, 34, 255) if clicked else (255, 255, 255, 255)
        txt_col = (255, 255, 255, 255) if clicked else (12, 12, 12, 255)
        txt = text_sprite(label.replace(" ✓", ""), c.font, size, txt_col, 0, (0, 0, 0, 0))
        press = 1 - 0.12 * punch_envelope(dt - 0.85, 0.03, 10)
        w, h = (txt.width + 90 * s) * press, (size * 1.9) * press
        scale = ease_out_back(enter, 2) * (1 - 0.3 * leave)
        bw, bh = w * scale, h * scale
        a = int(255 * (1 - leave))
        draw.rounded_rectangle((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), radius=bh / 2,
                               fill=fill[:3] + (a,))
        paste(canvas, txt, cx - (14 * s if clicked else 0), cy, scale, 0, 1 - leave)
        if clicked:
            ch = icon("star", int(size * 0.7), c.color("text"), c.color("accent"))
            paste(canvas, ch, cx + txt.width * scale / 2 + 10 * s, cy, scale, 0, 1 - leave)
            bell = icon("bell", int(size * 1.2), c.color("text"), c.color("accent"))
            ring = math.sin(dt * 30) * 18 * math.exp(-3 * max(0.0, dt - 1.0))
            paste(canvas, bell, cx + bw / 2 + 60 * s, cy, scale, ring, 1 - leave)
        # curseur qui vient cliquer
        if dt < 1.3:
            p = ease_out_cubic(min(1.0, dt / 0.8))
            px = cx + bw * 0.25 + (1 - p) * c.width * 0.3
            py = cy + bh * 0.2 + (1 - p) * c.height * 0.12
            r = 16 * s * (1 - 0.3 * punch_envelope(dt - 0.85, 0.03, 10))
            draw.ellipse((px - r, py - r, px + r, py + r), fill=(255, 255, 255, 230),
                         outline=(0, 0, 0, 255), width=max(2, int(3 * s)))


ELEMENTS = {
    "word": Word, "hook": Hook, "notify": Notify, "headshot": Headshot, "combo": Combo,
    "victory": Victory, "title": Title, "lower_third": LowerThird, "scoreboard": Scoreboard,
    "hud": Hud, "progress": Progress, "sparks": Particles, "shockwave": Shockwave,
    "explosion": Explosion, "embers": Embers, "subscribe": Subscribe,
}


def build(timeline: list[dict], ctx: Context) -> list[Element]:
    elements = []
    for item in timeline:
        cls = ELEMENTS.get(item["type"])
        if cls is None:
            raise ValueError(f"élément graphique inconnu : {item['type']}")
        elements.append(cls(item, ctx))
    return sorted(elements, key=lambda e: e.layer)


def render_frame(elements: list[Element], ctx: Context, t: float) -> Image.Image | None:
    active = [e for e in elements if e.active(t)]
    if not active:
        return None
    canvas = Image.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 0))
    for e in active:
        # un ImageDraw « RGBA » mélange correctement les couleurs semi-transparentes
        e.draw(canvas, ImageDraw.Draw(canvas, "RGBA"), t)
    return canvas


def render_track(timeline: list[dict], ctx: Context, out: Path,
                 on_progress=None, cancel_check=None) -> dict:
    """Encode la couche graphique (QuickTime Animation avec alpha) de la durée du montage."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    elements = build(timeline, ctx)
    frames = int(round(ctx.duration * ctx.fps))
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{ctx.width}x{ctx.height}",
           "-r", f"{ctx.fps:g}", "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            **_popen_kwargs())
    blank = bytes(ctx.width * ctx.height * 4)
    drawn = 0
    started = time.time()
    try:
        assert proc.stdin is not None
        for i in range(frames):
            t = i / ctx.fps
            img = render_frame(elements, ctx, t)
            if img is None:
                proc.stdin.write(blank)
            else:
                drawn += 1
                proc.stdin.write(img.tobytes())
            if on_progress and i % 30 == 0:
                on_progress(i / max(frames, 1))
            if cancel_check and i % 30 == 0 and cancel_check():
                raise RuntimeError("rendu graphique interrompu")
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"encodage de la couche graphique : {err[-400:]}")
    counts: dict[str, int] = {}
    for item in timeline:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return {"frames": frames, "frames_with_graphics": drawn, "elements": counts,
            "seconds": round(time.time() - started, 2)}
