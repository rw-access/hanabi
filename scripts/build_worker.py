"""Stage the public board and the Python modules used by online games."""
from pathlib import Path
from shutil import copyfile, rmtree
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hanabi.server import TEAMMATES

MODULES = ("__init__.py", "rooms.py", "game.py", "beliefs.py", "bots.py", "group_policy.py")
ASSETS = ("index.html", "app.js", "room-client.js", "styles.css", "cards.css", "deck.webp", "icon.png")


def build():
    if not (ROOT / "python_modules" / "workers" / "__init__.py").is_file():
        raise SystemExit("Install the Python Workers SDK following worker/README.md before building.")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    # Stage a complete release first so stale files cannot enter the public bundle.
    with TemporaryDirectory(dir=dist) as temp:
        stage = Path(temp)
        package = stage / "worker" / "hanabi"
        package.mkdir(parents=True)
        copyfile(ROOT / "worker" / "entry.py", package.parent / "entry.py")
        for name in MODULES:
            copyfile(ROOT / "hanabi" / name, package / name)

        board = stage / "assets" / "play" / "hanabi"
        board.mkdir(parents=True)
        for name in ASSETS:
            copyfile(ROOT / "hanabi" / "static" / name, board / name)
        index = board / "index.html"
        html = index.read_text()
        teammate, rules = TEAMMATES["adaptive"]
        replacements = {
            '<html lang="en">': '<html lang="en" data-rooms="enabled">',
            "{{teammate}}": teammate,
            "{{teammate_rules}}": rules,
            "{{recording_hidden}}": "hidden",
        }
        for template, value in replacements.items():
            if template not in html:
                raise ValueError(f"Missing board template marker {template}")
            html = html.replace(template, value)
        index.write_text(html)

        for name in ("worker", "assets"):
            dest = dist / name
            if dest.exists():
                rmtree(dest)
            (stage / name).rename(dest)


if __name__ == "__main__":
    build()
