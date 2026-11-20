"""Exécuté par l'interpréteur d'un bac à sable (venv isolé), jamais importé par l'application.

Entrée  : JSON sur stdin  {"video": "...", "threshold": 27.0}
Sortie  : JSON sur stdout {"cuts": [secondes...], "version": "..."}
"""
import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    import scenedetect
    from scenedetect import ContentDetector, detect

    scenes = detect(payload["video"], ContentDetector(threshold=float(payload.get("threshold", 27.0))))
    cuts = [round(start.get_seconds(), 3) for start, _ in scenes[1:]]
    print(json.dumps({"cuts": cuts, "version": getattr(scenedetect, "__version__", "?")}))


if __name__ == "__main__":
    main()
