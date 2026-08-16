#!/usr/bin/env python3
"""
Regenerate everything: campaign data -> interactive viz -> plain-language explainer.

Cross-platform replacement for build.sh, which needs a POSIX shell and so does not
run in PowerShell. Same three steps, same outputs.

    python build.py              # campaign + viz
    python build.py --report     # also attempt the write-up (needs figure snapshots)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(script: str) -> None:
    print(f"    $ {sys.executable} {script}")
    r = subprocess.run([sys.executable, str(ROOT / script)], cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"{script} failed with exit code {r.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figs", default=str(ROOT / "docs/figs"),
                    help="directory of dashboard screenshots for the write-up")
    args = ap.parse_args()

    print("==> 1/3  running the 12-round campaign")
    run("make_timelapse_data.py")

    print("==> 2/3  building the interactive timelapse")
    tpl = (ROOT / "viz/mdao_timelapse.template.html").read_text(encoding="utf-8")
    data = json.dumps(
        json.loads((ROOT / "timelapse_data.json").read_text(encoding="utf-8")),
        separators=(",", ":"),
    )
    out = ROOT / "docs/timelapse.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(tpl.replace("__DATA__", data), encoding="utf-8")
    if "__DATA__" in out.read_text(encoding="utf-8"):
        raise SystemExit("template placeholder not substituted -- check viz template")
    print(f"    docs/timelapse.html  {out.stat().st_size // 1024} KB")

    print("==> 3/3  building the plain-language explainer")
    figs = Path(args.figs)
    if figs.is_dir() and any(figs.glob("*.png")):
        run("build_writeup.py")
    else:
        print(f"    figures not found in {figs} -- writing the explainer without them.")
        print("    To capture them, open docs/timelapse.html, screenshot the map and")
        print("    the belief panel at rounds 0/2/5/6/8/11 as map_NN.png / belief_NN.png,")
        print("    then: python build_writeup.py --figs <dir>")
        run("build_writeup.py")

    print("\ndone.  Next, if you want the design shortlist:")
    print("    python report_designs.py")
    print("    python make_design_report.py --data top_designs.json "
          "--out docs/design_shortlist.pdf     (needs: pip install reportlab)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
