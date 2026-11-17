"""Agent Video Editor : exécute un plan de montage avec FFmpeg.

Étapes : (1) chaque passage est extrait et mis au format cible dans un fichier
intermédiaire de haute qualité ; (2) assemblage final avec transitions,
musique (atténuée sous le son du jeu), effets sonores, sous-titres et
normalisation du volume. Les sources ne sont ouvertes qu'en lecture.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Callable

from ..ffutil import (BUILTIN_ENCODE_PROFILE, FFmpegError, MediaInfo, probe,
                      resolve_encoder_family, run_ffmpeg)
from . import subtitles as subs
from .plan import EditPlan

AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
XFADE_STYLES = ["fade", "slideleft", "fade", "slideup", "fade", "smoothleft"]


def video_filter(plan: EditPlan, layout: str = "hybrid", fg_aspect: float = 0.8) -> str:
    """Chaîne de filtres vidéo : entrée [0:v:0] -> sortie [v]."""
    w, h, fps = plan.width, plan.height, plan.fps
    tail = f"setsar=1,fps={fps:g},format=yuv420p[v]"
    if h <= w:  # horizontal : mise à l'échelle + bandes si le ratio diffère
        return (f"[0:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,{tail}")
    if layout == "crop":
        return (f"[0:v:0]crop='min(iw,ih*{w}/{h})':ih,scale={w}:{h}:flags=bicubic,{tail}")
    aspect = max(w / h, fg_aspect) if layout == "hybrid" else None
    fg = (f"crop='min(iw,ih*{aspect:.4f})':ih,scale={w}:-2:flags=bicubic" if aspect
          else f"scale={w}:-2:flags=bicubic")
    bw, bh = w // 4 // 2 * 2, h // 4 // 2 * 2
    return (f"[0:v:0]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"gblur=sigma=10,scale={w}:{h}:flags=bilinear,eq=brightness=-0.08[bg];"
            f"[fgsrc]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}")


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


class Renderer:
    def __init__(self, cfg, encode_profile: dict | None = None, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.profile = encode_profile or BUILTIN_ENCODE_PROFILE
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

    # -- étape 1 : clips intermédiaires ------------------------------------------
    def _render_clip(self, plan: EditPlan, index: int, out: Path, infos: dict[str, MediaInfo],
                     on_progress=None) -> None:
        seg = plan.segments[index]
        info = infos[seg.source]
        d = seg.duration
        fc = self.cfg[plan.format]
        vf = video_filter(plan, fc.get("layout", "hybrid"), float(fc.get("fg_aspect", 0.8)))
        inputs = ["-ss", f"{seg.start:.6f}", "-t", f"{d + 0.5:.6f}", "-i", seg.source]
        fade = (f"afade=t=in:d=0.03,apad,atrim=0:{d:.6f},"
                f"afade=t=out:st={max(0.0, d - 0.03):.6f}:d=0.03")
        fmt = "aresample=48000:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if not info.has_audio:
            inputs += ["-f", "lavfi", "-t", f"{d:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
            af = f"[1:a]{fmt},{fade}[a]"
        elif self.cfg["render"]["audio_tracks"] == "mix" and info.audio_streams > 1:
            ins = "".join(f"[0:a:{i}]" for i in range(info.audio_streams))
            af = f"{ins}amix=inputs={info.audio_streams}:normalize=0,{fmt},{fade}[a]"
        else:
            af = f"[0:a:0]{fmt},{fade}[a]"

        def build(family: str) -> list[str]:
            return [*inputs, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
                    *self._codec(family, "mezzanine"), "-c:a", "pcm_s16le",
                    "-t", f"{d:.6f}", str(out)]

        self._encode(build, duration=d, on_progress=on_progress)

    # -- étape 2 : assemblage final ----------------------------------------------
    def _assemble(self, plan: EditPlan, clips: list[Path], out: Path, work: Path,
                  music: Path | None, whoosh: Path | None, ass_name: str | None,
                  on_progress=None) -> dict:
        n = len(clips)
        durations = [s.duration for s in plan.segments]
        t = plan.transition_seconds if plan.transition == "xfade" and n > 1 else 0.0
        total = sum(durations) - (n - 1) * t
        inputs: list[str] = []
        for clip in clips:
            inputs += ["-i", str(clip)]
        graph: list[str] = []
        transition_times: list[float] = []

        if n == 1:
            graph.append("[0:v]null[vc];[0:a]anull[ac]")
        elif t > 0:
            prev_v, prev_a, offset = "[0:v]", "[0:a]", 0.0
            for k in range(1, n):
                offset += durations[k - 1] - t
                transition_times.append(offset)
                style = XFADE_STYLES[(k - 1) % len(XFADE_STYLES)]
                graph.append(f"{prev_v}[{k}:v]xfade=transition={style}:duration={t:.4f}:"
                             f"offset={offset:.4f}[xv{k}]")
                graph.append(f"{prev_a}[{k}:a]acrossfade=d={t:.4f}:c1=tri:c2=tri[xa{k}]")
                prev_v, prev_a = f"[xv{k}]", f"[xa{k}]"
            graph.append(f"{prev_v}null[vc];{prev_a}anull[ac]")
        else:
            pads = "".join(f"[{k}:v][{k}:a]" for k in range(n))
            graph.append(f"{pads}concat=n={n}:v=1:a=1[vc][ac]")
            acc = 0.0
            for d in durations[:-1]:
                acc += d
                transition_times.append(acc)

        fade_out = max(0.0, total - 0.35)
        vchain = f"[vc]fade=t=in:d=0.12,fade=t=out:st={fade_out:.3f}:d=0.35"
        if ass_name:
            vchain += f",subtitles={ass_name}"
        graph.append(vchain + ",format=yuv420p[vout]")

        mix_inputs = ["[game]"]
        next_input = n
        used_music = None
        need_key = bool(music and self.cfg["music"]["ducking"])
        graph.append("[ac]asplit=2[game][key]" if need_key else "[ac]anull[game]")
        if music:
            inputs += ["-stream_loop", "-1", "-i", str(music)]
            vol = float(self.cfg["music"]["volume_db"])
            chain = (f"[{next_input}:a]aresample=48000,aformat=sample_fmts=fltp:"
                     f"channel_layouts=stereo,atrim=0:{total:.3f},volume={vol}dB,"
                     f"afade=t=in:d=0.8,afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5")
            if need_key:
                graph.append(chain + "[mraw]")
                graph.append("[mraw][key]sidechaincompress=threshold=0.04:ratio=6:"
                             "attack=15:release=400[music]")
            else:
                graph.append(chain + "[music]")
            mix_inputs.append("[music]")
            used_music = music.name
            next_input += 1
        sfx_count = 0
        if whoosh and transition_times and plan.transition == "xfade":
            inputs += ["-i", str(whoosh)]
            k = len(transition_times)
            vol = float(self.cfg["sfx"]["volume_db"])
            outs = "".join(f"[w{i}]" for i in range(k))
            graph.append(f"[{next_input}:a]aresample=48000,aformat=sample_fmts=fltp:"
                         f"channel_layouts=stereo,volume={vol}dB,asplit={k}{outs}")
            for i, at in enumerate(transition_times):
                delay = max(0, int((at - 0.12) * 1000))
                graph.append(f"[w{i}]adelay={delay}:all=1[wd{i}]")
                mix_inputs.append(f"[wd{i}]")
            sfx_count = k
            next_input += 1
        lufs = float(self.cfg["render"]["loudness_lufs"])
        mixed = "".join(mix_inputs)
        mix = (f"{mixed}amix=inputs={len(mix_inputs)}:normalize=0:duration=first,"
               if len(mix_inputs) > 1 else f"{mixed}")
        graph.append(f"{mix}loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,"
                     f"afade=t=out:st={fade_out:.3f}:d=0.35[aout]")

        def build(family: str) -> list[str]:
            return [*inputs, "-filter_complex", ";".join(graph),
                    "-map", "[vout]", "-map", "[aout]", *self._codec(family, "final"),
                    "-r", f"{plan.fps:g}", "-c:a", "aac", "-b:a",
                    self.cfg["render"]["audio_bitrate"], "-ar", "48000",
                    "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]

        self._encode(build, duration=total, on_progress=on_progress, cwd=work)
        return {"music": used_music, "sfx": sfx_count, "duration": total}

    # -- API ---------------------------------------------------------------------
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
        total_clip = sum(s.duration for s in plan.segments) or 1.0
        done = 0.0
        clips: list[Path] = []
        for i, seg in enumerate(plan.segments):
            clip = work / f"clip_{i:03d}.mkv"
            base = done

            def clip_progress(p: float, base=base, d=seg.duration) -> None:
                if on_progress:
                    on_progress(0.65 * (base + p * d) / total_clip)

            self._render_clip(plan, i, clip, infos, clip_progress)
            done += seg.duration
            clips.append(clip)

        ass_name = None
        subtitle_origin = "none"
        if cues_by_source:
            cues = subs.remap_cues(plan, cues_by_source)
            if cues:
                ass_name = "subs.ass"
                subs.write_ass(cues, work / ass_name, plan.width, plan.height,
                               int(self.cfg["subtitles"]["font_size"]))
                subtitle_origin = f"{len(cues)} répliques"

        music = None
        if self.cfg["music"]["enabled"]:
            music = pick_music(self.cfg.path("music"), out_path.stem)
        whoosh = find_sfx(self.cfg.path("sfx"), "whoosh") if self.cfg["sfx"]["enabled"] else None

        partial = out_path.with_name(out_path.stem + ".partial" + out_path.suffix)
        details = self._assemble(
            plan, clips, partial, work, music, whoosh, ass_name,
            (lambda p: on_progress(0.65 + 0.35 * p)) if on_progress else None)
        partial.replace(out_path)
        if not self.cfg["render"]["keep_work_files"]:
            shutil.rmtree(work, ignore_errors=True)
        return {
            "path": str(out_path), "format": plan.format, "encoder": self.family,
            "clips": len(clips), "render_seconds": round(time.time() - started, 2),
            "subtitles": subtitle_origin, **details,
        }
