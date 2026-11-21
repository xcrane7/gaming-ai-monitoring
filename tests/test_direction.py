"""Directeur artistique : classes prouvées, effets adaptés au contexte, moments faibles
laissés tranquilles, moments importants mis en valeur, textes jamais inventés."""
from test_engines import _fake_analysis

from gaming_ai_editor.editor.plan import build_plan
from gaming_ai_editor.engines import direction, editing


def ev(kind, strength=0.8, label=None, **kw):
    return {"t": 10.0, "kind": kind, "strength": strength, "label": label, **kw}


def test_different_events_get_different_decisions():
    cases = {"kill": ev("impact", label="kill"), "headshot": ev("shot", label="headshot"),
             "explosion": ev("explosion"), "reaction": ev("reaction"),
             "tension": ev("surprise"), "victoire": ev("impact", label="victoire"),
             "fail": ev("impact", label="fail"), "drole": ev("impact", label="drôle")}
    decisions = {name: direction.decide(e) for name, e in cases.items()}
    assert {n: d["class"] for n, d in decisions.items()} == {n: n for n in cases}
    recipes = [tuple(d["fx"]) for d in decisions.values()]
    assert len(set(recipes)) == len(recipes), "chaque classe a sa propre réalisation"


def test_effects_match_the_context():
    kill = direction.decide(ev("impact", 0.7, "kill"))["fx"]
    assert {"punch", "hitmarker_gfx", "sfx_impact", "shake_micro"} <= set(kill)
    head = direction.decide(ev("shot", 0.8, "headshot"))["fx"]
    assert {"punch_snap", "flash", "headshot_gfx", "sfx_boom"} <= set(head)
    multi = direction.decide(ev("burst", 0.8, "kill", count=3))
    assert multi["class"] == "multikill" and "combo" in multi["fx"]
    win = direction.decide(ev("impact", 0.6, "victoire"))["fx"]
    assert {"victory_gfx", "glow", "sfx_riser"} <= set(win)
    clutch = direction.decide(ev("impact", 0.7, "clutch"), role="climax")["fx"]
    assert "prefer_ramp" in clutch and "sfx_riser" in clutch
    assert direction.decide(ev("reaction", 0.9))["fx"] == ["punch_soft"], \
        "la voix du joueur suffit : rien qui la couvre"
    assert "punch_comic" in direction.decide(ev("impact", 0.6, "drôle"))["fx"]


def test_weak_moments_get_no_excessive_effects():
    weak = direction.decide(ev("impact", 0.2))
    assert weak["class"] == "deplacement" and weak["fx"] == [] and weak["tier"] == "aucun"
    assert direction.decide(ev("rush", 0.3))["fx"] == []
    assert direction.decide({"t": 1, "kind": "", "strength": 0})["fx"] == []
    mild = direction.decide(ev("impact", 0.6))
    assert not direction.HEAVY & set(mild["fx"]), "action sans étiquette : pas d'effet lourd"


def test_important_events_are_emphasized():
    climax_kill = direction.decide(ev("impact", 0.9, "kill"), role="climax", peak_near=True)
    plain_hit = direction.decide(ev("impact", 0.6), role="build")
    assert climax_kill["importance"] > plain_hit["importance"]
    assert climax_kill["tier"] == "fort" and len(climax_kill["fx"]) > len(plain_hit["fx"])
    # deux effets lourds trop proches : le second perd ses effets lourds, pas le reste
    calm = direction.decide(ev("explosion", 0.9), heavy_ok=False)
    assert "explosion_gfx" in calm["fx"] and not direction.HEAVY & set(calm["fx"])


def test_no_invented_information_without_ai_or_ocr():
    """Sans étiquette IA/OCR : jamais de kill, de headshot, de bannière ni de texte de kill."""
    for kind in ("impact", "shot", "burst", "explosion"):
        d = direction.decide(ev(kind, 0.95, count=4))
        assert d["class"] not in ("kill", "headshot", "multikill", "victoire", "clutch")
        assert not {"notify", "headshot_gfx", "victory_gfx", "hitmarker_gfx"} & set(d["fx"])
    kill_words = set(direction.TEXTS["kill"] + direction.TEXTS["headshot"]
                     + direction.TEXTS["multikill"] + direction.TEXTS["victoire"])
    assert not kill_words & set(direction.TEXTS["spectaculaire"] + direction.TEXTS["explosion"])


def test_director_in_the_real_pipeline(cfg):
    """De bout en bout : un kill confirmé par Claude reçoit hit marker et bannière ; les
    impacts sans étiquette reçoivent une réalisation neutre ; les textes suivent la classe."""
    a = _fake_analysis("/x/v.mp4")
    a["highlights"][1].update({"source": "claude", "label": "kill"})
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "k", None)
    events = [e for s in plan.segments for e in s.events]
    assert all("direction" in e for e in events)
    kill = [e for e in events if e["direction"]["class"] == "kill"]
    assert kill and "hitmarker_gfx" in kill[0]["fx"] and "notify" in kill[0]["fx"]
    kinds = {g["type"] for g in plan.graphics}
    assert "hitmarker" in kinds and "notify" in kinds
    others = [e for e in events if e["direction"]["class"] != "kill"]
    assert others and all("hitmarker_gfx" not in e["fx"] for e in others)
    allowed = {w for pool in direction.TEXTS.values() for w in pool}
    words = [g["text"] for g in plan.graphics if g["type"] == "word"]
    assert words and set(words) <= allowed, words
    assert any(n.startswith("Direction : ") and "kill" in n for n in plan.notes)


def test_one_ai_label_never_becomes_a_multikill(cfg):
    """Défaut constaté : un seul moment « kill » contenant deux sons devenait un multi-kill."""
    a = _fake_analysis("/x/v.mp4")
    a["highlights"][1].update({"source": "claude", "label": "kill"})  # sons à 30,0 et 31,5 s
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, None, "k", None)
    classes = [e["direction"]["class"] for s in plan.segments for e in s.events]
    assert classes.count("kill") == 1 and "multikill" not in classes, classes


def test_hooks_are_checked_and_never_claim_without_proof():
    ok, why = direction.check_hook("Il ne l'a pas vue venir", set())
    assert ok == "Il ne l'a pas vu venir" and not why, "accord du participe corrigé"
    assert direction.check_hook("Ce kill est fou", set())[0] == "", "kill sans preuve : refusé"
    assert direction.check_hook("Ce kill est fou", {"kill"})[0] == "Ce kill est fou"
    assert direction.check_hook("Un moment absolument épique", {"kill"})[0] == ""
    assert direction.check_hook("Wow", set())[0] == ""


def test_claude_hook_rejected_falls_back_to_local_template(cfg):
    a = _fake_analysis("/x/v.mp4")
    plan = build_plan("short", [a], cfg)
    editing.direct(plan, [a], cfg, {"hook": "Triple kill incroyable"}, "k", None)
    assert plan.hook and "kill" not in plan.hook.lower()
    assert plan.hook in editing.HOOK_TEMPLATES["explosion"] + editing.HOOKS
    assert any("Accroche de l'IA refusée" in n for n in plan.notes)
