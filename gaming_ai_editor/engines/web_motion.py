"""Moteur de motion design HTML/CSS/SVG rendu par Chromium (Playwright), image par image.

Pourquoi : Pillow dessine des formes fixes image par image ; les animations complexes
(courbes de Bézier, masques animés, typographie avec tracking, flou de mouvement, découpes
« glitch », tracés SVG) y sont longues à écrire et pauvres. Chromium les fait nativement.

Comment (déterministe, jamais en temps réel) :
- une seule page HTML contient tous les éléments complexes du montage ; chaque animation
  est une Web Animation (élément.animate) mise en pause ;
- pour l'image t, `seek(t)` place toutes les animations à t (currentTime), puis une capture
  avec fond transparent donne une image RGBA ;
- Chromium n'est lancé que si le montage contient de tels éléments, et seules les images où
  l'un d'eux est visible sont capturées (le reste du temps : rien) ;
- l'image est fusionnée avec la couche Pillow (sous-titres, particules, barre de progression)
  dans la même piste transparente : le compositing FFmpeg ne change pas.

Si Playwright ou Chromium manque ou échoue, tout repasse par Pillow (comme avant).
"""
from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path

from PIL import Image

# éléments rendus par Chromium ; les autres restent dans Pillow
WEB_TYPES = {"hook", "word", "focus_ring", "notify", "headshot", "combo", "victory", "climax",
             "hitmarker"}

# Cinq directions artistiques. Polices : celles de Windows d'abord, puis Linux.
STYLES = {
    "competitive": {
        "label": "Esport : précis, rapide, minimaliste",
        "font": "Bahnschrift, 'DIN Alternate', 'Inter Display', 'DejaVu Sans', sans-serif",
        "weight": 800, "case": "uppercase", "track": "0.02em", "track_in": "0.18em",
        "ease_in": "cubic-bezier(.2,.9,.1,1)", "ease_out": "cubic-bezier(.7,0,.84,0)",
        "in_s": 0.22, "stagger": 0.045, "skew": 0, "rgb": 3, "blur_in": 6, "glow": 0,
        "word_scale": 0.85, "word_entry": "slide", "ring": 0.9, "letterbox": False,
        "gradient": False, "camera": {"punch": 0.75, "shake": 0.5}, "transitions": "smooth",
        "word_gap": 2.0,
    },
    "cinematic": {
        "label": "Cinématique : élégant, profondeur, transitions douces",
        "font": "'Segoe UI', 'Inter Display', 'DejaVu Sans', sans-serif",
        "weight": 600, "case": "uppercase", "track": "0.16em", "track_in": "0.45em",
        "ease_in": "cubic-bezier(.16,1,.3,1)", "ease_out": "cubic-bezier(.4,0,1,1)",
        "in_s": 0.6, "stagger": 0.09, "skew": 0, "rgb": 0, "blur_in": 14, "glow": 0,
        "word_scale": 0.72, "word_entry": "rise", "ring": 0.7, "letterbox": True,
        "gradient": False, "camera": {"punch": 0.45, "shake": 0.25}, "transitions": "smooth",
        "word_gap": 3.0,
    },
    "aggressive": {
        "label": "Agressif : impacts puissants, zooms, flashs contrôlés",
        "font": "Impact, 'Inter Display Black', 'Inter Display', 'DejaVu Sans', sans-serif",
        "weight": 900, "case": "uppercase", "track": "0.01em", "track_in": "0.3em",
        "ease_in": "cubic-bezier(.2,1.7,.35,1)", "ease_out": "cubic-bezier(.7,0,.84,0)",
        "in_s": 0.24, "stagger": 0.055, "skew": -8, "rgb": 9, "blur_in": 18, "glow": 0,
        "word_scale": 1.0, "word_entry": "slam", "ring": 1.0, "letterbox": False,
        "gradient": False, "camera": {"punch": 1.2, "shake": 1.15}, "transitions": "aggressive",
        "word_gap": 1.6,
    },
    "epic": {
        "label": "Épique : explosions, particules, animations élaborées",
        "font": "'Inter Display Black', Impact, 'Inter Display', 'DejaVu Sans', sans-serif",
        "weight": 900, "case": "uppercase", "track": "0.04em", "track_in": "0.35em",
        "ease_in": "cubic-bezier(.17,1.45,.4,1)", "ease_out": "cubic-bezier(.6,0,.9,.3)",
        "in_s": 0.34, "stagger": 0.07, "skew": 0, "rgb": 4, "blur_in": 16, "glow": 26,
        "word_scale": 1.0, "word_entry": "slam", "ring": 1.15, "letterbox": True,
        "gradient": True, "camera": {"punch": 1.0, "shake": 1.0}, "transitions": "aggressive",
        "word_gap": 1.8,
    },
    "clean": {
        "label": "Clean : moderne, sans surcharge",
        "font": "'Segoe UI', 'Inter Display', 'DejaVu Sans', sans-serif",
        "weight": 700, "case": "none", "track": "0em", "track_in": "0.08em",
        "ease_in": "cubic-bezier(.22,1,.36,1)", "ease_out": "cubic-bezier(.64,0,.78,0)",
        "in_s": 0.4, "stagger": 0.06, "skew": 0, "rgb": 0, "blur_in": 8, "glow": 0,
        "word_scale": 0.7, "word_entry": "rise", "ring": 0.6, "letterbox": False,
        "gradient": False, "camera": {"punch": 0.4, "shake": 0.0}, "transitions": "smooth",
        "word_gap": 3.5,
    },
}


def choose_style(cfg, dna: dict) -> str:
    """Style imposé par la configuration, sinon déduit de l'énergie de la vidéo."""
    want = str(((cfg.get("motion") or {}).get("style")) or "auto").lower()
    if want in STYLES:
        return want
    energy, family = float(dna.get("energy", 0.7)), dna.get("transitions", "smooth")
    if energy > 0.92:
        return "epic"
    if family == "aggressive":
        return "aggressive"
    if family == "glitchy":
        return "competitive"
    return "cinematic" if energy < 0.65 else "competitive"


def chromium_path(cfg=None) -> str | None:
    """Chromium à utiliser : config, puis navigateur de Playwright (None = celui de Playwright)."""
    explicit = str(((cfg or {}).get("motion") or {}).get("chromium_path") or "") if cfg else ""
    if explicit and Path(explicit).exists():
        return explicit
    cands = [os.environ.get("GAE_CHROMIUM")]
    root = Path("/opt/pw-browsers")  # navigateur préinstallé (environnement de test Linux)
    if root.exists():
        cands += [str(p) for p in sorted(root.glob("chromium-*/chrome-linux64/chrome"))]
    for cand in cands:
        if cand and Path(cand).exists():
            return str(cand)
    return None  # Playwright prend son propre Chromium (python -m playwright install chromium)


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def item_window(item: dict) -> tuple[float, float]:
    start = float(item.get("start", 0.0))
    end = float(item.get("end", start + float(item.get("life", 0.9))))
    return start, end


# ---------------------------------------------------------------------------------------------
# page HTML : bibliothèque de composants animés (Web Animations, mises en pause)
# ---------------------------------------------------------------------------------------------
_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden}
*{box-sizing:border-box}
.abs{position:absolute;left:0;top:0;will-change:transform,opacity}
.txt{white-space:nowrap;line-height:1;font-kerning:normal}
.mask{display:inline-block;overflow:hidden;vertical-align:bottom;padding:0 .04em .08em}
</style></head><body><script>
const D = __DATA__;
const W = D.width, H = D.height, S = D.style, P = D.palette, U = Math.min(W, H) / 1080;
const ticks = [];
function el(tag, css, parent) {
  const n = document.createElement(tag); if (css) n.style.cssText = css;
  (parent || document.body).appendChild(n); return n; }
function svg(tag, attrs, parent) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]); parent.appendChild(n); return n; }
// entrée : remplit aussi l'avant (invisible avant le début) ; sortie : seulement l'après
function enter(n, frames, t, d, easing) {
  return n.animate(frames, {delay: t * 1000, duration: Math.max(1, d * 1000), easing: easing || S.ease_in, fill: 'both'}); }
function leave(n, frames, t, d, easing) {
  return n.animate(frames, {delay: t * 1000, duration: Math.max(1, d * 1000), easing: easing || S.ease_out, fill: 'forwards'}); }
function textStyle(n, px) {
  n.style.fontFamily = S.font; n.style.fontWeight = S.weight; n.style.textTransform = S.case;
  n.style.fontSize = px + 'px'; n.style.letterSpacing = S.track; n.style.color = P.text || '#fff';
  n.style.textShadow = `0 ${0.06 * px}px 0 rgba(0,0,0,.55), 0 0 ${0.22 * px}px rgba(0,0,0,.45)`;
  // contour sombre dessiné DERRIÈRE les lettres : lisible sur une explosion ou un ciel clair
  n.style.webkitTextStroke = `${Math.max(2, 0.085 * px)}px rgba(0,0,0,.85)`; n.style.paintOrder = 'stroke fill'; }
function fit(n, maxW, maxH) {   // réduit la police jusqu'à ce que le texte tienne : jamais coupé
  let px = parseFloat(n.style.fontSize);
  for (let i = 0; i < 60 && (n.scrollWidth > maxW || (maxH && n.scrollHeight > maxH)); i++) {
    px *= 0.94; n.style.fontSize = px + 'px'; }
  return px; }
function gradientText(n) {   // dégradé lisible : contour en filtre (une ombre de texte
  // apparaîtrait à travers les lettres transparentes et salirait l'or)
  n.style.backgroundImage = `linear-gradient(180deg, #ffffff 8%, ${P.accent} 62%, ${P.accent} 100%)`;
  n.style.webkitBackgroundClip = 'text'; n.style.color = 'transparent'; n.style.textShadow = 'none';
  n.style.filter = `drop-shadow(0 ${5 * U}px 0 rgba(0,0,0,.7)) drop-shadow(0 0 ${10 * U}px rgba(0,0,0,.45))`; }

// ---- accroche : mots révélés par masque, tracking qui se resserre, mot clé surligné ----------
function hook(it) {
  const box = el('div', `position:absolute;left:${W * 0.47 - W * 0.42}px;top:${H * 0.095}px;width:${W * 0.84}px;text-align:center;line-height:1.08`);
  const px0 = 112 * U;
  const words = String(it.text || '').split(/\s+/).filter(Boolean);
  const key = words.reduce((a, w, i) => w.length > words[a].length ? i : a, 0);
  const inner = [];
  words.forEach((w, i) => {
    const m = el('span', '', box); m.className = 'mask';
    const s = el('span', 'display:inline-block;position:relative;padding:0 .08em', m);
    s.textContent = w; textStyle(s, px0); inner.push([s, i]);
    if (i === key) { const bg = el('span', `position:absolute;left:0;right:0;top:12%;bottom:4%;background:${P.accent};z-index:-1;transform-origin:left center`, s);
      s.style.color = '#0b0b0b'; s.style.textShadow = 'none'; s.style.webkitTextStroke = '0'; s.style.zIndex = 0; s.style.isolation = 'isolate';
      enter(bg, [{transform: 'scaleX(0)'}, {transform: 'scaleX(1)'}], it.start + 0.12 + i * S.stagger, S.in_s * 1.2); }
    box.appendChild(document.createTextNode(' '));
  });
  const px = fit(box, W * 0.86, H * 0.25);
  inner.forEach(([s]) => s.style.fontSize = px + 'px');
  if (S.skew) box.style.transform = `skewX(${S.skew}deg)`;
  inner.forEach(([s, i]) => enter(s, [
      {transform: 'translateY(110%)', letterSpacing: S.track_in, filter: `blur(${S.blur_in * U}px)`},
      {transform: 'translateY(0)', letterSpacing: S.track, filter: 'blur(0px)'}],
      it.start + i * S.stagger, S.in_s));
  const out = Math.max(it.start + 0.6, it.end - 0.28);
  leave(box, [{opacity: 1, transform: `translateY(0) skewX(${S.skew}deg)`},
              {opacity: 0, transform: `translateY(${-40 * U}px) skewX(${S.skew}deg)`}], out, 0.26);
}

// ---- mot d'impact : entrée « slam / slide / rise », flou de mouvement, séparation RVB --------
function word(it) {
  const px0 = (it.size || 120) * U * (S.word_scale || 1);
  const pos = it.pos || [0.47, 0.56];
  const outer = el('div', `position:absolute;left:${pos[0] * W}px;top:${pos[1] * H}px;transform:translate(-50%,-50%) rotate(${(it.angle || 0) * 0.6}deg)`);
  const holder = el('div', 'position:relative', outer);
  const main = el('div', '', holder); main.className = 'txt'; main.textContent = it.text || '';
  textStyle(main, px0);
  // marge pour la dérive (+6 %), l'inclinaison, la séparation RVB et le halo : jamais coupé
  const room = 0.86 / 1.06 - (S.skew ? 0.05 : 0) - (S.rgb ? 0.02 : 0) - (S.glow ? 0.03 : 0);
  const px = fit(main, W * room); main.style.fontSize = px + 'px';
  if (S.gradient) gradientText(main);
  if (S.glow) main.style.filter = (main.style.filter || '') + ` drop-shadow(0 0 ${S.glow * U}px ${P.accent}88)`;
  const layers = [];
  if (S.rgb) for (const [c, dx] of [['#ff2a6d', -1], ['#05d9e8', 1]]) {
    const l = el('div', `position:absolute;left:0;top:0;color:${c};mix-blend-mode:screen;opacity:.85`, holder);
    l.className = 'txt'; l.textContent = it.text || ''; textStyle(l, px); l.style.color = c; l.style.textShadow = 'none'; l.style.webkitTextStroke = '0';
    holder.insertBefore(l, main); layers.push([l, dx]); }
  const t0 = it.start, life = Math.max(0.5, it.end - it.start);
  // l'entrée du style l'emporte : un « slam » n'existe que dans les styles qui en ont un
  const entry = S.word_entry || 'slam';
  const sk = S.skew || 0;
  const from = entry === 'slam' ? `scale(2.6) skewX(${sk}deg)` : entry === 'slide' ? `translateX(${-0.35 * W}px) skewX(-20deg)` : `translateY(${0.06 * H}px) scale(.9)`;
  enter(holder, [{transform: from, filter: `blur(${S.blur_in * U}px)`, opacity: 0},
                 {transform: `scale(1) skewX(${sk}deg)`, filter: 'blur(0px)', opacity: 1, offset: 0.75},
                 {transform: `scale(1) skewX(${sk}deg)`, filter: 'blur(0px)', opacity: 1}], t0, S.in_s);
  enter(outer, [{scale: 1}, {scale: 1.06}], t0 + S.in_s, Math.max(0.1, life - S.in_s), 'linear');
  layers.forEach(([l, dx]) => enter(l, [{transform: `translateX(${dx * S.rgb * 2.5 * U}px)`},
      {transform: `translateX(${dx * S.rgb * 0.35 * U}px)`}], t0, S.in_s * 1.6));
  leave(holder, [{opacity: 1, transform: `scale(1) skewX(${sk}deg)`, filter: 'blur(0px)'},
                 {opacity: 0, transform: `scale(.72) skewX(${sk}deg)`, filter: `blur(${8 * U}px)`}], it.end - 0.18, 0.18);
}

// ---- cible : tracé SVG qui se dessine, crochets qui se verrouillent, onde ---------------------
function focusRing(it) {
  const r = (it.radius || 0.12) * W * (S.ring || 1), size = r * 4;
  const x = (it.x ?? 0.5) * W, y = (it.y ?? 0.45) * H;
  const box = el('div', `position:absolute;left:${x - size / 2}px;top:${y - size / 2}px;width:${size}px;height:${size}px`);
  const s = svg('svg', {width: size, height: size, viewBox: `0 0 ${size} ${size}`}, box);
  const c = size / 2, sw = Math.max(3, 9 * U), circ = 2 * Math.PI * r;
  const shadow = svg('circle', {cx: c, cy: c, r, fill: 'none', stroke: 'rgba(0,0,0,.5)', 'stroke-width': sw + 6 * U}, s);
  const ring = svg('circle', {cx: c, cy: c, r, fill: 'none', stroke: P.accent, 'stroke-width': sw,
    'stroke-linecap': 'round', 'stroke-dasharray': circ, transform: `rotate(-90 ${c} ${c})`}, s);
  const pulse = svg('circle', {cx: c, cy: c, r, fill: 'none', stroke: P.accent, 'stroke-width': sw * 0.6}, s);
  const t0 = it.start, life = (it.end ?? it.start + (it.life || 0.9)) - t0;
  for (const n of [ring, shadow]) enter(n, [{strokeDashoffset: circ}, {strokeDashoffset: 0}], t0, 0.3);
  enter(pulse, [{transform: `translate(${c}px,${c}px) scale(1) translate(${-c}px,${-c}px)`, opacity: 0.9},
                {transform: `translate(${c}px,${c}px) scale(1.9) translate(${-c}px,${-c}px)`, opacity: 0}], t0 + 0.28, 0.45, 'cubic-bezier(.2,.8,.2,1)');
  const arm = r * 0.45, k = r * 1.05;
  for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const bx = c + sx * k, by = c + sy * k;
    const g = svg('g', {}, s);
    svg('path', {d: `M ${bx} ${by - sy * arm} L ${bx} ${by} L ${bx - sx * arm} ${by}`, fill: 'none', stroke: 'rgba(0,0,0,.5)', 'stroke-width': sw + 6 * U}, g);
    svg('path', {d: `M ${bx} ${by - sy * arm} L ${bx} ${by} L ${bx - sx * arm} ${by}`, fill: 'none', stroke: P.text || '#fff', 'stroke-width': sw, 'stroke-linecap': 'square'}, g);
    enter(g, [{transform: `translate(${sx * r * 0.9}px,${sy * r * 0.9}px)`, opacity: 0},
              {transform: 'translate(0,0)', opacity: 1}], t0, 0.32, 'cubic-bezier(.2,1.5,.4,1)'); }
  const dot = svg('circle', {cx: c, cy: c, r: Math.max(4, 8 * U), fill: P.accent}, s);
  enter(dot, [{opacity: 0}, {opacity: 1}], t0 + 0.2, 0.08, 'linear');
  enter(box, [{transform: 'rotate(-35deg) scale(1.25)'}, {transform: 'rotate(0deg) scale(1)'}], t0, 0.35);
  enter(box, [{opacity: 0}, {opacity: 1}], t0, 0.04, 'linear');  // rien avant l'instant
  leave(box, [{opacity: 1}, {opacity: 0}], t0 + life * 0.72, life * 0.28);
}

// ---- bannière (kill confirmé, etc.) : barre biseautée qui balaie, texte qui glisse ------------
function notify(it) {
  const y = H * (0.30 + 0.085 * (it.slot || 0)), h = 112 * U;
  const box = el('div', `position:absolute;left:${W * 0.05}px;top:${y}px;height:${h}px;display:flex;align-items:center`);
  const bar = el('div', `position:absolute;inset:0;background:${P.accent};clip-path:polygon(0 0,100% 0,calc(100% - ${h * 0.45}px) 100%,0 100%)`, box);
  const ic = el('div', `position:relative;width:${h}px;height:${h}px;display:flex;align-items:center;justify-content:center`, box);
  const s = svg('svg', {width: h * 0.62, height: h * 0.62, viewBox: '0 0 24 24'}, ic);
  svg('circle', {cx: 12, cy: 12, r: 8, fill: 'none', stroke: '#0b0b0b', 'stroke-width': 2.6}, s);
  svg('path', {d: 'M12 1v6M12 17v6M1 12h6M17 12h6', stroke: '#0b0b0b', 'stroke-width': 2.6}, s);
  const t = el('div', `position:relative;padding:0 ${h * 0.75}px 0 ${h * 0.1}px`, box); t.className = 'txt';
  t.textContent = it.text || ''; textStyle(t, h * 0.52); t.style.color = '#0b0b0b'; t.style.textShadow = 'none'; t.style.webkitTextStroke = '0';
  fit(t, W * 0.7);
  enter(bar, [{clipPath: `inset(0 100% 0 0)`}, {clipPath: 'inset(0 0% 0 0)'}], it.start, 0.22);
  enter(t, [{transform: `translateX(${-30 * U}px)`, opacity: 0}, {transform: 'translateX(0)', opacity: 1}], it.start + 0.1, 0.22);
  enter(s, [{transform: 'rotate(-120deg) scale(.4)'}, {transform: 'rotate(0) scale(1)'}], it.start + 0.05, 0.3);
  leave(box, [{clipPath: 'inset(0 0 0 0%)', opacity: 1}, {clipPath: 'inset(0 0 0 100%)', opacity: 1}], it.end - 0.22, 0.22);
}

// ---- headshot : viseur qui se verrouille, tampon avec découpes glitch -----------------------
function headshot(it) {
  const x = W * 0.47, y = H * 0.42, size = 300 * U;
  const box = el('div', `position:absolute;left:${x - size / 2}px;top:${y - size / 2}px;width:${size}px;height:${size}px`);
  const s = svg('svg', {width: size, height: size, viewBox: '0 0 100 100'}, box);
  svg('circle', {cx: 50, cy: 50, r: 30, fill: 'none', stroke: '#ff2a3d', 'stroke-width': 4}, s);
  svg('path', {d: 'M50 4v24M50 72v24M4 50h24M72 50h24', stroke: '#ffffff', 'stroke-width': 4}, s);
  enter(box, [{transform: 'scale(3) rotate(45deg)', opacity: 0}, {transform: 'scale(1) rotate(0)', opacity: 1}], it.start, 0.25, 'cubic-bezier(.2,1.4,.4,1)');
  const t = el('div', `position:absolute;left:${x}px;top:${y + size * 0.62}px;transform:translateX(-50%)`); t.className = 'txt';
  t.textContent = 'HEADSHOT'; textStyle(t, 120 * U); t.style.color = '#ff2a3d'; fit(t, W * 0.86);
  enter(t, [{letterSpacing: '.5em', opacity: 0, clipPath: 'inset(0 0 60% 0)'},
            {letterSpacing: '.3em', opacity: 1, clipPath: 'inset(40% 0 0 0)', offset: 0.35},
            {letterSpacing: '.12em', opacity: 1, clipPath: 'inset(0 0 0 0)', offset: 0.6},
            {letterSpacing: S.track, opacity: 1, clipPath: 'inset(0 0 0 0)'}], it.start + 0.12, 0.36, 'steps(6, end)');
  leave(box, [{opacity: 1}, {opacity: 0}], it.end - 0.25, 0.25);
  leave(t, [{opacity: 1}, {opacity: 0}], it.end - 0.25, 0.25);
}

// ---- combo / série : compteur qui saute à chaque coup, jauge segmentée ----------------------
function combo(it) {
  const hits = it.hits || [it.start];
  const x = W * 0.08, y = H * 0.36;
  const box = el('div', `position:absolute;left:${x}px;top:${y}px`);
  const lab = el('div', '', box); lab.className = 'txt'; lab.textContent = it.text || 'SÉRIE';
  textStyle(lab, 58 * U); lab.style.letterSpacing = '.2em';
  const num = el('div', `position:relative;height:${170 * U}px;width:${300 * U}px`, box);
  hits.forEach((h, k) => {
    const n = el('div', 'position:absolute;left:0;top:0', num); n.className = 'txt';
    n.textContent = 'x' + (k + 1); textStyle(n, 160 * U); n.style.color = k === hits.length - 1 ? P.accent : (P.text || '#fff');
    if (S.gradient && k === hits.length - 1) gradientText(n);
    enter(n, [{transform: 'scale(2.1)', opacity: 0, filter: `blur(${10 * U}px)`}, {transform: 'scale(1)', opacity: 1, filter: 'blur(0)'}], h, 0.18, 'cubic-bezier(.2,1.6,.4,1)');
    if (k < hits.length - 1) leave(n, [{opacity: 1}, {opacity: 0}], hits[k + 1], 0.06, 'linear'); });
  const meter = el('div', `display:flex;gap:${8 * U}px;margin-top:${10 * U}px`, box);
  hits.forEach((h, k) => {
    const seg = el('div', `width:${70 * U}px;height:${18 * U}px;background:rgba(255,255,255,.28);transform:skewX(-20deg)`, meter);
    const fill = el('div', `width:100%;height:100%;background:${P.accent};transform-origin:left center`, seg);
    enter(fill, [{transform: 'scaleX(0)'}, {transform: 'scaleX(1)'}], h, 0.12); });
  enter(box, [{transform: `translateX(${-60 * U}px)`, opacity: 0}, {transform: 'translateX(0)', opacity: 1}], it.start, 0.2);
  leave(box, [{opacity: 1, transform: 'translateX(0)'}, {opacity: 0, transform: `translateX(${-60 * U}px)`}], it.end - 0.25, 0.25);
}

// ---- victoire : deux barres qui se croisent, texte révélé par masque -------------------------
function victory(it) {
  const y = H * 0.42, h = 190 * U;
  const box = el('div', `position:absolute;left:0;top:${y - h / 2}px;width:${W}px;height:${h}px`);
  const a = el('div', `position:absolute;left:0;top:0;width:100%;height:${h * 0.12}px;background:${P.accent}`, box);
  const b = el('div', `position:absolute;left:0;bottom:0;width:100%;height:${h * 0.12}px;background:${P.accent}`, box);
  const band = el('div', 'position:absolute;left:0;top:12%;width:100%;height:76%;background:rgba(0,0,0,.55)', box);
  const t = el('div', 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%)', box); t.className = 'txt';
  t.textContent = it.text || 'VICTOIRE'; textStyle(t, 130 * U); fit(t, W * 0.86);
  if (S.gradient) gradientText(t);
  enter(a, [{transform: `translateX(${-W}px)`}, {transform: 'translateX(0)'}], it.start, 0.3);
  enter(b, [{transform: `translateX(${W}px)`}, {transform: 'translateX(0)'}], it.start, 0.3);
  enter(band, [{clipPath: 'inset(50% 0 50% 0)'}, {clipPath: 'inset(0 0 0 0)'}], it.start + 0.15, 0.3);
  enter(t, [{clipPath: 'inset(0 50% 0 50%)', letterSpacing: '.02em'}, {clipPath: 'inset(0 0% 0 0%)', letterSpacing: '.12em'}], it.start + 0.3, 0.45);
  leave(box, [{opacity: 1}, {opacity: 0}], it.end - 0.3, 0.3);
}

// ---- climax : bandes cinéma qui entrent, liseré d'accent ------------------------------------
function climax(it) {
  const h = H * 0.065;
  for (const top of [true, false]) {
    const bar = el('div', `position:absolute;left:0;${top ? 'top' : 'bottom'}:0;width:${W}px;height:${h}px;background:#000;border-${top ? 'bottom' : 'top'}:${Math.max(2, 4 * U)}px solid ${P.accent}`);
    enter(bar, [{transform: `translateY(${top ? -h - 8 : h + 8}px)`}, {transform: 'translateY(0)'}], it.start, 0.4);
    leave(bar, [{transform: 'translateY(0)'}, {transform: `translateY(${top ? -h - 8 : h + 8}px)`}], it.end - 0.35, 0.35); }
}

// ---- hit marker (kill confirmé) : quatre traits qui claquent autour du viseur ----------------
function hitmarker(it) {
  const size = 150 * U, x = (it.x ?? 0.5) * W, y = (it.y ?? 0.45) * H;
  const box = el('div', `position:absolute;left:${x - size / 2}px;top:${y - size / 2}px;width:${size}px;height:${size}px`);
  const s = svg('svg', {width: size, height: size, viewBox: '0 0 100 100'}, box);
  const strokes = [];
  for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const g = svg('g', {}, s);
    const d = `M ${50 + sx * 12} ${50 + sy * 12} L ${50 + sx * 34} ${50 + sy * 34}`;
    svg('path', {d, stroke: 'rgba(0,0,0,.6)', 'stroke-width': 13, 'stroke-linecap': 'round'}, g);
    svg('path', {d, stroke: '#ffffff', 'stroke-width': 7, 'stroke-linecap': 'round'}, g);
    enter(g, [{transform: `translate(${-sx * 10}px,${-sy * 10}px)`}, {transform: 'translate(0,0)'}], it.start, 0.09, 'cubic-bezier(.2,1.8,.4,1)'); }
  enter(box, [{transform: 'scale(1.5)', opacity: 0}, {transform: 'scale(1)', opacity: 1}], it.start, 0.08, 'cubic-bezier(.2,1.4,.4,1)');
  leave(box, [{opacity: 1, transform: 'scale(1)'}, {opacity: 0, transform: 'scale(.85)'}], it.end - 0.2, 0.2);
}

const BUILD = {hook, word, focus_ring: focusRing, notify, headshot, combo, victory, climax, hitmarker};
window.build = async () => {
  await document.fonts.ready;
  for (const it of D.items) {
    const before = new Set(document.body.children);
    try { (BUILD[it.type] || (() => {}))(it); } catch (e) { console.error(e); }
    // garde-fou : tout nouvel élément racine est caché avant le début de son composant
    for (const n of document.body.children) if (!before.has(n) && n.tagName !== 'SCRIPT')
      n.animate([{visibility: 'hidden'}, {visibility: 'visible'}], {delay: it.start * 1000, duration: 1, fill: 'both'}); }
  for (const a of document.getAnimations()) a.pause();
  return document.getAnimations().length; };
window.seek = (ms) => { for (const a of document.getAnimations()) a.currentTime = ms; for (const f of ticks) f(ms / 1000); };
</script></body></html>"""


_SHARED: dict = {}


def _browser(chromium: str | None):
    """Chromium partagé par tout le processus : lancé une fois (≈1 s), réutilisé à chaque
    rendu, fermé à la sortie. Playwright refuse deux lancements simultanés dans un fil."""
    import atexit

    from playwright.sync_api import sync_playwright

    b = _SHARED.get("browser")
    if b is not None and b.is_connected():
        return b
    if "pw" not in _SHARED:
        _SHARED["pw"] = sync_playwright().start()
        atexit.register(shutdown)
    opts = {"args": ["--no-sandbox", "--disable-dev-shm-usage", "--force-color-profile=srgb",
                     "--hide-scrollbars"]}
    if chromium:
        opts["executable_path"] = chromium
    _SHARED["browser"] = _SHARED["pw"].chromium.launch(**opts)
    return _SHARED["browser"]


def shutdown() -> None:
    b, pw = _SHARED.pop("browser", None), _SHARED.pop("pw", None)
    for close in ((b.close if b else None), (pw.stop if pw else None)):
        try:
            if close:
                close()
        except Exception:
            pass


class WebMotion:
    """Rend les éléments complexes d'une piste graphique avec Chromium."""

    def __init__(self, items: list[dict], width: int, height: int, palette: dict,
                 style: str = "aggressive", chromium: str | None = None):
        self.items = [dict(i) for i in items if i.get("type") in WEB_TYPES]
        for it in self.items:
            it["start"], it["end"] = item_window(it)
        self.width, self.height = int(width), int(height)
        self.palette = dict(palette or {})
        self.style_name = style if style in STYLES else "aggressive"
        self.style = STYLES[self.style_name]
        self.chromium = chromium
        self._page = None
        self.frames = 0

    def html(self) -> str:
        data = {"width": self.width, "height": self.height, "style": self.style,
                "palette": {"accent": "#FFC531", "accent2": "#9B5CFF", "text": "#FFFFFF",
                            **self.palette}, "items": self.items}
        return _HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))

    def __enter__(self):
        try:
            self._page = _browser(self.chromium).new_page(viewport={"width": self.width,
                                                          "height": self.height},
                                                device_scale_factor=1)
            self._page.set_content(self.html())
            self.animations = int(self._page.evaluate("build()"))
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        """Ferme la page ; le navigateur partagé reste ouvert pour le rendu suivant."""
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        self._page = None

    def active(self, t: float) -> bool:
        return any(it["start"] - 0.02 <= t <= it["end"] + 0.02 for it in self.items)

    def frame(self, t: float) -> Image.Image | None:
        """Image RGBA de l'instant t, ou None si aucun élément n'est visible."""
        if self._page is None or not self.active(t):
            return None
        self._page.evaluate(f"seek({t * 1000:.3f})")
        png = self._page.screenshot(omit_background=True, type="png")
        self.frames += 1
        img = Image.open(io.BytesIO(png))
        return img.convert("RGBA") if img.mode != "RGBA" else img


def open_engine(items: list[dict], width: int, height: int, palette: dict, style: str,
                cfg=None) -> WebMotion | None:
    """Démarre le moteur si utile et possible ; None sinon (repli Pillow)."""
    if not any(i.get("type") in WEB_TYPES for i in items) or not available():
        return None
    eng = WebMotion(items, width, height, palette, style, chromium_path(cfg))
    return eng.__enter__()


def doctor() -> dict:
    """État du moteur (pour l'inspection de l'environnement)."""
    ok = available()
    return {"playwright": ok, "chromium": chromium_path() or ("Playwright" if ok else None),
            "which_chrome": shutil.which("chrome") or shutil.which("chromium")}
