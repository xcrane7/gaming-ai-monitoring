"""8. Automatic Rendering Engine — composition multicouche avec FFmpeg.

Couches, de bas en haut :

    1. gameplay      recadrage (suivi de l'action), speed ramp, caméra, VFX, étalonnage
    2. transitions   xfade choisies par le réalisateur
    3. graphismes    couche transparente du moteur de motion graphics
    4. sous-titres   ASS (libass)
    5. audio         son du jeu (ralenti grave pendant les speed ramps)
                     + musique (générée ou fournie, atténuée sous le jeu)
                     + piste d'effets sonores
                     -> normalisation du volume (−14 LUFS)

Étapes : chaque clip est rendu en fichier intermédiaire de haute qualité (NVENC
si disponible, sinon CPU), puis la couche graphique et les pistes audio sont
générées, puis tout est assemblé en une passe. Les sources restent en lecture seule.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Callable

from ..engines import audio as audio_engine
from ..engines import color as color_engine
from ..engines import graphics as gfx_engine
from ..engines import vfx
from ..engines.motion import CameraMove, _f, camera_filters, expr_piecewise, motion_blur
from ..ffutil import (BUILTIN_ENCODE_PROFILE, FFmpegError, MediaInfo, filter_available, probe,
                      resolve_encoder_family, run_ffmpeg)
from . import subtitles as subs
from .plan import EditPlan, Segment

AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
XFADE_STYLES = ["fade", "slideleft", "fade", "slideup", "fade", "smoothleft"]


def layout_filter(plan: EditPlan, layout: str, fg_aspect: float = 0.8,
                  focus: list | None = None, size: tuple[int, int] | None = None) -> str:
    """Mise au format de sortie (sans étiquettes) : entrée quelconque -> W x H.

    `size` permet de produire une image plus grande que la sortie (sur-échantillonnage) :
    les zooms de caméra recadrent alors dans de vrais pixels au lieu d'agrandir l'image.
    """
    w, h = size or (plan.width, plan.height)
    if h <= w:  # horizontal : mise à l'échelle + bandes si le ratio diffère
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    if layout in ("track", "crop"):
        x = "(iw-ow)/2"
        if layout == "track" and focus and len(focus) > 1:
            fx = expr_piecewise([(float(t), float(v)) for t, v in focus])
            x = f"clip(({fx})*iw-ow/2,0,iw-ow)"
        return f"crop='min(iw,ih*{w}/{h})':ih:x='{x}':y=0,scale={w}:{h}:flags=bicubic"
    aspect = max(w / h, fg_aspect) if layout == "hybrid" else None
    fg = (f"crop='min(iw,ih*{aspect:.4f})':ih,scale={w}:-2:flags=bicubic" if aspect
          else f"scale={w}:-2:flags=bicubic")
    bw, bh = w // 4 // 2 * 2, h // 4 // 2 * 2
    return (f"split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"gblur=sigma=10,scale={w}:{h}:flags=bilinear,eq=brightness=-0.08[bg];"
            f"[fgsrc]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")


def video_filter(plan: EditPlan, layout: str = "hybrid", fg_aspect: float = 0.8) -> str:
    """Ancienne chaîne simple (sans effets) : entrée [0:v:0] -> sortie [v]."""
    tail = f"setsar=1,fps={plan.fps:g},format=yuv420p[v]"
    return f"[0:v:0]{layout_filter(plan, layout, fg_aspect)},{tail}"


def pick_music(music_dir: Path, key: str) -> Path | None:
    files = sorted(p for p in music_dir.glob("*") if p.suffix.lower() in AUDIO_EXT)
    if not files:
        return None
    return files[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(files)]


def find_sfx(sfx_dir: Path, name: str) -> Path | None:
    for p in sorted(sfx_dir.glob(f"{name}.*")):
        if p.suffix.lower() in AUDIO_EXT:
            return p
    return None


def styled(plan: EditPlan) -> bool:
    return bool(plan.style) and bool(plan.graphics or any(s.camera for s in plan.segments))


class Renderer:
    def __init__(self, cfg, encode_profile: dict | None = None, log: Callable[[str], None] = print,
                 motion_preset: dict | None = None):
        self.cfg = cfg
        self.profile = encode_profile or BUILTIN_ENCODE_PROFILE
        self.preset = motion_preset or {}
        self.family = resolve_encoder_family(cfg["render"]["encoder"])
        self.log = log

    # -- encodage avec repli CPU ------------------------------------------------
    def _encode(self, build_args: Callable[[str], list[str]], **kwargs) -> None:
        try:
            run_ffmpeg(build_args(self.family), **kwargs)
        except FFmpegError as exc:
            if self.family == "cpu":
                raise
            self.log(f"Encodage {self.family} en échec, repli sur le CPU ({str(exc)[:160]})")
            self.family = "cpu"
            run_ffmpeg(build_args("cpu"), **kwargs)

    def _codec(self, family: str, stage: str) -> list[str]:
        profile = self.profile.get(family) or BUILTIN_ENCODE_PROFILE[family]
        return list(profile.get(stage) or BUILTIN_ENCODE_PROFILE[family][stage])

    # -- couche 1 : un clip de gameplay -------------------------------------------
    def _clip_video_graph(self, plan: EditPlan, seg: Segment, layout: str, cube: Path | None) -> str:
        fc = self.cfg[plan.format]
        fps = plan.fps
        d = seg.out_duration
        ramp = seg.speed_ramp()
        chain = ["setpts=PTS-STARTPTS"]
        if ramp:
            # ralenti progressif : nouvelle horloge, puis fusion d'images pour la fluidité
            chain += [f"setpts='{ramp.setpts_expr()}'", f"framerate=fps={fps:g}"]
        else:
            chain.append(f"fps={fps:g}")
        cam = seg.camera or {}
        size = None
        k_ss = float(self.cfg["render"].get("supersample", 1.0))
        if cam and k_ss > 1.0:  # punch-in net : on zoome dans une image plus grande
            size = (int(plan.width * k_ss) // 2 * 2, int(plan.height * k_ss) // 2 * 2)
        lay = layout_filter(plan, layout, float(fc.get("fg_aspect", 0.8)), seg.focus, size)
        graph = f"[0:v:0]{','.join(chain)}[pre];[pre]{lay},setsar=1"
        k = float(plan.style.get("intensity", 1.0)) if plan.style else 1.0
        if cam:
            move = CameraMove(
                duration=d, push=float(cam.get("push", 0.04)),
                punches=[tuple(p) for p in cam.get("punches", [])],
                shakes=[tuple(p) for p in cam.get("shakes", [])],
                beats=list(cam.get("beats", [])),
                smooth_zoom=tuple(cam["smooth_zoom"]) if cam.get("smooth_zoom") else None,
                pan=float(cam.get("pan", 0.0)), rotate=bool(cam.get("rotate")))
            graph += "," + camera_filters(move, plan.width, plan.height, 1.0, self.preset)
        fx = seg.vfx or {}
        filters = []
        if fx.get("motion_blur"):
            filters.append(motion_blur([tuple(w) for w in fx["motion_blur"]]))
        if fx.get("flash"):
            filters.append(vfx.impact_flash([tuple(x) for x in fx["flash"]],
                                            float(self.preset.get("flash", 1.0))))
        if fx.get("rgb_split"):
            filters.append(vfx.rgb_split([tuple(w) for w in fx["rgb_split"]],
                                         int(plan.width / 90 * max(k, 0.5))))
        if fx.get("glitch"):
            filters.append(vfx.glitch([tuple(w) for w in fx["glitch"]], plan.width))
        if fx.get("distortion"):
            filters.append(vfx.distortion([tuple(w) for w in fx["distortion"]], k))
        filters = [f for f in filters if f]
        if filters:
            graph += "," + ",".join(filters)
        label = "cv"
        graph += f"[{label}]"
        if fx.get("glow"):
            g, label = vfx.glow([tuple(w) for w in fx["glow"]], plan.width, label)
            graph += ";" + g
        if fx.get("cinematic_blur"):
            g, label = vfx.cinematic_blur(label, plan.width, 0.4)
            graph += ";" + g
        tail = []
        if cube is not None:
            tail.append(color_engine.grade_filter(cube, k))
        if plan.style:
            impacts = [float(p[0]) for p in cam.get("punches", [])]
            tail.append(vfx.vignette(impacts, 0.5))
        tail.append("format=yuv420p")
        graph += f";[{label}]{','.join(tail)}[v]"
        return graph

    def _tempo(self, rate: float, slow: bool) -> str:
        """Filtre audio qui change la vitesse du son d'un facteur `rate`."""
        mode = self.cfg["style"].get("slowmo_audio", "keep_pitch") if slow else "keep_pitch"
        if mode == "deep":  # son ralenti et plus grave, façon « bullet time »
            return f"asetrate={int(48000 * rate)},aresample=48000,lowpass=f=2800"
        if filter_available("rubberband"):  # hauteur conservée, meilleure qualité
            return f"rubberband=tempo={rate:.5f}:pitch=1"
        parts, r = [], rate  # atempo accepte 0,5 à 2 par instance : on enchaîne
        while r < 0.5:
            parts.append("atempo=0.5")
            r /= 0.5
        while r > 2.0:
            parts.append("atempo=2.0")
            r /= 2.0
        parts.append(f"atempo={r:.5f}")
        return ",".join(parts)

    def _clip_audio_graph(self, seg: Segment, info: MediaInfo, has_input_audio: bool,
                          lead: float = 0.0, lead_next: float = 0.0, ain: int = 0) -> str:
        """Son du clip. `lead` : le son commence `lead` s avant l'image (coupe en J) ;
        `lead_next` : il s'arrête d'autant plus tôt, le clip suivant ayant pris le relais."""
        d = seg.out_duration
        length = d + lead - lead_next
        fmt = "aresample=48000:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo"
        fade = (f"afade=t=in:d=0.03,apad,atrim=0:{length:.6f},"
                f"afade=t=out:st={max(0.0, length - 0.03):.6f}:d=0.03")
        if not has_input_audio:
            return f"[{ain}:a]{fmt},{fade}[a]"
        if self.cfg["render"]["audio_tracks"] == "mix" and info.audio_streams > 1:
            ins = "".join(f"[{ain}:a:{i}]" for i in range(info.audio_streams))
            src = f"{ins}amix=inputs={info.audio_streams}:normalize=0,{fmt}"
        else:
            src = f"[{ain}:a:0]{fmt}"
        ramp = seg.speed_ramp()
        if not ramp:
            return f"{src},{fade}[a]"
        a, b = ramp.start + lead, ramp.end + lead
        return (f"{src},asetpts=PTS-STARTPTS,asplit=3[s1][s2][s3];"
                f"[s1]atrim=0:{_f(a)},asetpts=PTS-STARTPTS[p1];"
                f"[s2]atrim={_f(a)}:{_f(b)},asetpts=PTS-STARTPTS,"
                f"{self._tempo(ramp.audio_rate(), ramp.slow)}[p2];"
                f"[s3]atrim={_f(b)},asetpts=PTS-STARTPTS[p3];"
                f"[p1][p2][p3]concat=n=3:v=0:a=1,{fmt},{fade}[a]")

    def audio_leads(self, plan: EditPlan) -> list[float]:
        """Avance du son de chaque clip (coupes en J) : format long uniquement."""
        jl = float(self.cfg["long"].get("jl_cuts", 0.0)) if plan.format == "long" else 0.0
        if jl <= 0 or len(plan.segments) < 2:
            return [0.0] * len(plan.segments)
        leads = [0.0]
        for seg in plan.segments[1:]:
            leads.append(round(min(jl, seg.start, max(0.0, seg.duration - 1.0)), 4))
        return leads

    def _render_clip(self, plan: EditPlan, index: int, out: Path, infos: dict[str, MediaInfo],
                     on_progress=None, work: Path | None = None, cube: Path | None = None) -> None:
        seg = plan.segments[index]
        info = infos[seg.source]
        d = seg.out_duration
        fc = self.cfg[plan.format]
        layout = (plan.style or {}).get("layout") or fc.get("layout", "hybrid")
        if layout == "auto":
            layout = "crop"
        inputs = ["-ss", f"{seg.start:.6f}", "-t", f"{seg.duration + 0.5:.6f}", "-i", seg.source]
        leads = self.audio_leads(plan)
        lead = leads[index]
        lead_next = leads[index + 1] if index + 1 < len(leads) else 0.0
        ain = 0
        if not info.has_audio:
            inputs += ["-f", "lavfi", "-t", f"{d + lead:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
            ain = 1
        elif lead > 0:  # coupe en J : le son est lu depuis un peu avant l'image
            inputs += ["-ss", f"{seg.start - lead:.6f}", "-t", f"{seg.duration + lead + 0.5:.6f}",
                       "-i", seg.source]
            ain = 1
        if styled(plan):
            vgraph = self._clip_video_graph(plan, seg, layout, cube)
        else:
            vgraph = video_filter(plan, layout, float(fc.get("fg_aspect", 0.8)))
        agraph = self._clip_audio_graph(seg, info, info.has_audio, lead, lead_next, ain)
        if vgraph.endswith("[v]"):  # l'image fait exactement la durée prévue (le son peut
            vgraph = vgraph[:-3] + f",trim=duration={d:.6f}[v]"  # déborder : coupe en J)

        def build(family: str) -> list[str]:
            return [*inputs, "-filter_complex", f"{vgraph};{agraph}", "-map", "[v]",
                    "-map", "[a]", *self._codec(family, "mezzanine"), "-c:a", "pcm_s16le",
                    "-t", f"{d + max(lead, 0.0):.6f}", str(out)]

        self._encode(build, duration=d, on_progress=on_progress, cwd=work)

    # -- couche 5 : audio ------------------------------------------------------------
    def _music(self, plan: EditPlan, out_stem: str, work: Path, total: float) -> tuple[Path | None, dict]:
        m = plan.music or {}
        if m.get("mode") == "generated":
            res = audio_engine.generate_music(
                work / "music.wav", total, bpm=float(m["bpm"]), drop_at=m.get("drop_at"),
                calm=tuple(m["calm"]) if m.get("calm") else None,
                seed=int((plan.style or {}).get("seed", 0)) % 100000,
                energy=float((plan.style or {}).get("energy", 1.0)),
                fade_out=0.05 if m.get("loop") else 1.5)
            return Path(res["path"]), {"music": "générée", "bpm": m["bpm"],
                                       "drop_at": m.get("drop_at")}
        if m.get("mode") == "user":
            return Path(m["path"]), {"music": Path(m["path"]).name, "bpm": m.get("bpm")}
        if m.get("mode") == "none" or not self.cfg["music"]["enabled"]:
            return None, {"music": None}
        found = pick_music(self.cfg.path("music"), out_stem)
        return found, {"music": found.name if found else None}

    @staticmethod
    def _music_envelope(plan: EditPlan) -> str:
        """Musique fournie : +3 dB pendant la montée vers le climax, chute nette 0,3 s avant
        le pic, puis retour au niveau normal au drop. (La musique générée le fait déjà.)"""
        m = plan.music or {}
        if m.get("mode") != "user" or not m.get("calm") or not m.get("drop_at"):
            return ""
        calm_a, drop = float(m["calm"][0]), float(m["drop_at"])
        bpm = float(m.get("bpm") or 120)
        build_a = max(0.0, calm_a - 0.3 - 8 * 60.0 / bpm)  # deux mesures de montée
        dip_a = max(build_a, calm_a - 0.3)
        rise = f"(1+0.413*(t-{_f(build_a)})/{_f(max(dip_a - build_a, 0.1))})"
        return (f"if(between(t,{_f(build_a)},{_f(dip_a)}),{rise},"
                f"if(between(t,{_f(dip_a)},{_f(drop)}),0.4,1))")

    @staticmethod
    def energy_arc(plan: EditPlan, dip_db: float = 4.0) -> str:
        """Courbe d'énergie du mix : cold open à plein niveau (l'accroche doit frapper), la
        montée repart {dip_db} dB plus bas et remonte régulièrement jusqu'au climax.
        Sans cela, la normalisation rendait toute la vidéo aussi forte : aucune montée
        (critique du rendu réel : énergie -0,9 dB du premier au dernier tiers)."""
        roles = [s.role for s in plan.segments]
        if "climax" not in roles:
            return ""
        offs = plan.offsets()
        t_climax = offs[roles.index("climax")]
        t_build = offs[1] if roles[0] == "teaser" and len(offs) > 1 else 0.0
        if t_climax - t_build < 4.0:
            return ""
        low = 10 ** (-dip_db / 20)
        ramp = f"({_f(low)}+{_f(1 - low)}*(t-{_f(t_build)})/{_f(t_climax - t_build)})"
        return f"volume='if(between(t,{_f(t_build)},{_f(t_climax)}),{ramp},1)':eval=frame,"

    def _sfx_track(self, plan: EditPlan, work: Path, total: float) -> tuple[Path | None, int]:
        cues = list(plan.sfx or [])
        if not cues and self.cfg["sfx"]["enabled"] and plan.transition == "xfade" \
                and len(plan.segments) > 1 and find_sfx(self.cfg.path("sfx"), "whoosh"):
            cues = [{"t": t, "kind": "whoosh", "gain": 1.0} for t in plan.junctions()]
        if not cues or not self.cfg["sfx"]["enabled"]:
            return None, 0
        samples = {}  # tes propres sons (assets/sfx/<type>.wav...) remplacent les synthétisés
        for kind in audio_engine.SFX_KINDS:
            found = find_sfx(self.cfg.path("sfx"), kind)
            if found is not None:
                try:
                    samples[kind] = audio_engine.load_mono(found, audio_engine.SR)
                except Exception:
                    continue
        path = audio_engine.render_sfx_track(cues, total, work / "sfx.wav", samples)
        return path, len(cues)

    # -- assemblage ----------------------------------------------------------------------
    def _assemble(self, plan: EditPlan, clips: list[Path], out: Path, work: Path,
                  music: Path | None, sfx_path: Path | None, gfx: Path | None,
                  ass_name: str | None, on_progress=None) -> dict:
        n = len(clips)
        laps = plan.overlaps()
        offsets = plan.offsets()
        t = max(laps) if laps else 0.0
        total = plan.duration
        inputs: list[str] = []
        for clip in clips:
            inputs += ["-i", str(clip)]
        graph: list[str] = []
        if n == 1:
            graph.append("[0:v]null[vc];[0:a]anull[ac]")
        elif t > 0:
            prev_v, prev_a = "[0:v]", "[0:a]"
            styles = plan.transitions or []
            whips = []
            for k in range(1, n):
                d = laps[k - 1]
                style = styles[k - 1] if k - 1 < len(styles) else \
                    XFADE_STYLES[(k - 1) % len(XFADE_STYLES)]
                if style.startswith("whip"):  # glissé très rapide + flou de mouvement
                    whips.append((offsets[k] - 0.06, offsets[k] + d + 0.06))
                    style = "slideleft" if style == "whipleft" else "slideright"
                graph.append(f"{prev_v}[{k}:v]xfade=transition={style}:duration={d:.4f}:"
                             f"offset={offsets[k]:.4f}[xv{k}]")
                graph.append(f"{prev_a}[{k}:a]acrossfade=d={d:.4f}:c1=tri:c2=tri[xa{k}]")
                prev_v, prev_a = f"[xv{k}]", f"[xa{k}]"
            blur = f",{motion_blur(whips)}" if whips else ""
            graph.append(f"{prev_v}null{blur}[vc];{prev_a}anull[ac]")
        else:
            # image et son concaténés séparément : avec les coupes en J, le son d'un clip
            # peut être un peu plus long que son image (il déborde sur le clip précédent)
            vpads = "".join(f"[{k}:v]" for k in range(n))
            apads = "".join(f"[{k}:a]" for k in range(n))
            graph.append(f"{vpads}concat=n={n}:v=1:a=0[vc];{apads}concat=n={n}:v=0:a=1[ac]")

        fade_out = max(0.0, total - 0.35)
        # boucle : ni fondu d'entrée ni fondu au noir, la relecture enchaîne sans coupure
        vchain = "[vc]null" if plan.loop else \
            f"[vc]fade=t=in:d=0.12,fade=t=out:st={fade_out:.3f}:d=0.35"
        if ass_name:
            vchain += f",subtitles={ass_name}"
        next_input = n
        if gfx is not None:
            inputs += ["-i", str(gfx)]
            graph.append(vchain + "[vb]")
            graph.append(f"[vb][{next_input}:v]overlay=format=auto:eof_action=pass,"
                         "format=yuv420p[vout]")
            next_input += 1
        else:
            graph.append(vchain + ",format=yuv420p[vout]")

        style = bool(plan.style)
        mix_inputs = ["[game]"]
        need_key = bool(music and self.cfg["music"]["ducking"])
        game_gain = "volume=-3dB," if style and music else ""
        graph.append(f"[ac]{game_gain}asplit=2[game][key]" if need_key else f"[ac]{game_gain}anull[game]")
        if music:
            loop = ["-stream_loop", "-1"] if (plan.music or {}).get("mode") != "generated" else []
            inputs += [*loop, "-i", str(music)]
            vol = float(self.cfg["music"]["volume_db"]) + (8.0 if style else 0.0)
            chain = (f"[{next_input}:a]aresample=48000,aformat=sample_fmts=fltp:"
                     f"channel_layouts=stereo,atrim=0:{total:.3f},volume={vol}dB")
            env = self._music_envelope(plan)
            if env:
                chain += f",volume='{env}':eval=frame"
            if not style:
                chain += f",afade=t=in:d=0.8,afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5"
            if need_key:
                graph.append(chain + "[mraw]")
                ratio = 3 if style else 6
                graph.append(f"[mraw][key]sidechaincompress=threshold=0.05:ratio={ratio}:"
                             "attack=15:release=300[music]")
            else:
                graph.append(chain + "[music]")
            mix_inputs.append("[music]")
            next_input += 1
        if sfx_path is not None:
            inputs += ["-i", str(sfx_path)]
            vol = float(self.cfg["sfx"]["volume_db"]) + (6.0 if style else 0.0)
            graph.append(f"[{next_input}:a]aresample=48000,aformat=sample_fmts=fltp:"
                         f"channel_layouts=stereo,volume={vol}dB[sfx]")
            mix_inputs.append("[sfx]")
            next_input += 1
        lufs = float(self.cfg["render"]["loudness_lufs"])
        mixed = "".join(mix_inputs)
        mix = (f"{mixed}amix=inputs={len(mix_inputs)}:normalize=0:duration=first,"
               if len(mix_inputs) > 1 else f"{mixed}")
        tail = (f"afade=t=out:st={max(0.0, total - 0.04):.3f}:d=0.04" if plan.loop
                else f"afade=t=out:st={fade_out:.3f}:d=0.35")
        arc = self.energy_arc(plan) if style and plan.format == "short" and \
            (plan.style or {}).get("energy_arc", self.cfg["style"].get("energy_arc", True)) else ""
        graph.append(f"{mix}{arc}loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,{tail}[aout]")

        def build(family: str) -> list[str]:
            return [*inputs, "-filter_complex", ";".join(graph),
                    "-map", "[vout]", "-map", "[aout]", *self._codec(family, "final"),
                    "-r", f"{plan.fps:g}", "-c:a", "aac", "-b:a",
                    self.cfg["render"]["audio_bitrate"], "-ar", "48000",
                    "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]

        self._encode(build, duration=total, on_progress=on_progress, cwd=work)
        return {"duration": total}

    # -- API ---------------------------------------------------------------------------------
    def render(self, plan: EditPlan, out_path: Path, work: Path,
               on_progress: Callable[[float], None] | None = None,
               cues_by_source: dict | None = None) -> dict:
        if not plan.segments:
            raise FFmpegError("Plan de montage vide : rien à rendre.")
        started = time.time()
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        infos = {src: probe(src) for src in {s.source for s in plan.segments}}
        is_styled = styled(plan)
        stages = {"clips": 0.55, "gfx": 0.15, "mix": 0.30} if is_styled else \
            {"clips": 0.65, "gfx": 0.0, "mix": 0.35}

        def report(base: float, span: float):
            return (lambda p: on_progress(base + span * p)) if on_progress else None

        cube = None
        look = (plan.style or {}).get("look")
        if is_styled and look:
            cube = color_engine.write_cube(work / f"{look}.cube", look,
                                           strength=0.6 + 0.4 * float(plan.style.get("intensity", 1)))
        total_clip = sum(s.out_duration for s in plan.segments) or 1.0
        done = 0.0
        clips: list[Path] = []
        timings = {}
        t0 = time.time()
        for i, seg in enumerate(plan.segments):
            clip = work / f"clip_{i:03d}.mkv"
            base = done
            d = seg.out_duration

            def clip_progress(p: float, base=base, d=d) -> None:
                if on_progress:
                    on_progress(stages["clips"] * (base + p * d) / total_clip)

            self._render_clip(plan, i, clip, infos, clip_progress, work, cube)
            done += d
            clips.append(clip)
        timings["clips"] = round(time.time() - t0, 2)

        ass_name = None
        subtitle_origin = "none"
        if cues_by_source:
            cues = subs.remap_cues(plan, cues_by_source)
            word_style = is_styled and self.cfg["subtitles"].get("style", "words") in (
                "words", "tiktok")
            if cues and word_style:  # sous-titres animés mot à mot dans la couche graphique
                words = subs.words_from_cues(cues)
                # pas de sous-titre pendant les 0,3 s d'un impact : on laisse voir l'action
                mute = [[round(g["start"], 3), round(g["start"] + 0.3, 3)] for g in plan.graphics
                        if g["type"] in ("explosion", "sparks", "headshot", "victory")]
                plan.graphics = [g for g in plan.graphics if g["type"] != "captions"] + [
                    {"type": "captions", "start": words[0]["t0"], "end": words[-1]["t1"],
                     "words": words, "mute": mute}]
                subtitle_origin = f"{len(cues)} répliques, {len(words)} mots animés"
            elif cues:
                ass_name = "subs.ass"
                subs.write_ass(cues, work / ass_name, plan.width, plan.height,
                               int(self.cfg["subtitles"]["font_size"]),
                               font=(plan.style or {}).get("font"))
                subtitle_origin = f"{len(cues)} répliques"

        total = plan.duration
        gfx_path = None
        gfx_stats: dict = {}
        if plan.graphics:
            t0 = time.time()
            ctx = gfx_engine.Context(
                plan.width, plan.height, plan.fps, total,
                palette=plan.style.get("palette") or gfx_engine.Context(1, 1, 1, 1).palette,
                font=plan.style.get("font") or "Impact",
                beats=self._montage_beats(plan, total), clips=plan.offsets())
            gfx_path = work / "graphics.mov"
            gfx_stats = gfx_engine.render_track(plan.graphics, ctx, gfx_path,
                                                report(stages["clips"], stages["gfx"]))
            timings["graphics"] = round(time.time() - t0, 2)

        music, music_info = self._music(plan, out_path.stem, work, total)
        sfx_path, sfx_count = self._sfx_track(plan, work, total)
        partial = out_path.with_name(out_path.stem + ".partial" + out_path.suffix)
        t0 = time.time()
        details = self._assemble(plan, clips, partial, work, music, sfx_path, gfx_path, ass_name,
                                 report(stages["clips"] + stages["gfx"], stages["mix"]))
        timings["assemble"] = round(time.time() - t0, 2)
        partial.replace(out_path)
        if not self.cfg["render"]["keep_work_files"]:
            shutil.rmtree(work, ignore_errors=True)
        return {
            "path": str(out_path), "format": plan.format, "encoder": self.family,
            "clips": len(clips), "render_seconds": round(time.time() - started, 2),
            "subtitles": subtitle_origin, "sfx": sfx_count, **music_info, **details,
            "graphics": gfx_stats, "timings": timings,
            "look": look if is_styled else None,
        }

    @staticmethod
    def _montage_beats(plan: EditPlan, total: float) -> list[float]:
        m = plan.music or {}
        if m.get("mode") == "user":
            return [b for b in m.get("beats", []) if b <= total]
        if m.get("mode") == "generated":
            beat = 60.0 / float(m["bpm"])
            return [round(i * beat, 4) for i in range(int(total / beat) + 1)]
        return []
