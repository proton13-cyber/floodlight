"""
Print your AVL build's raw strip-force table.

Run this when the strip coupling misbehaves -- "no strips usable for CL_max",
zero-lift strips, or an area fraction that isn't ~1.0. AVL versions differ in what the
FS table carries, and a column shift is silent: the file parses fine and every number
comes from the wrong place.

    AVL_BIN=/path/to/avl python3 aero/dump_fs.py

Prints the header AVL wrote, how this parser mapped it, and the first few rows with
each value labelled. If the mapping is wrong it will be obvious here and nowhere else.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aero.avl_runner import _strip_columns, find_avl, parse_strips  # noqa: E402
from aero.geometry import write_deck  # noqa: E402


def main() -> int:
    exe = find_avl()
    print(f"AVL binary: {exe}\n")

    work = Path(tempfile.mkdtemp(prefix="avl_fsdump_"))
    write_deck(work / "w.avl", S_ref=16.0, AR=12.0, t_c=0.12, sweep_c4=5.0, n_span=8)
    subprocess.run(
        [str(exe)], cwd=str(work), text=True, capture_output=True, timeout=120,
        input="LOAD w.avl\nOPER\nA C 0.6\nX\nFS s.fs\n\nQUIT\n",
        env={k: v for k, v in __import__("os").environ.items() if k != "DISPLAY"},
    )
    fs = work / "s.fs"
    if not fs.is_file():
        print("No FS file was written at all. That is the menu-grammar bug, not a "
              "column bug -- see aero/AGENTS.md failure 6.")
        return 1

    text = fs.read_text(errors="replace")
    print("--- raw table, as AVL wrote it " + "-" * 44)
    started = False
    for line in text.splitlines():
        if "Strip Forces referred to Strip Area" in line:
            started = True
        if started:
            print(line)
            if line.strip().startswith("4 ") or line.strip().startswith("4\t"):
                break
    print("-" * 75)

    header = next(
        (ln for ln in text.splitlines() if _strip_columns(ln) is not None), None
    )
    if header is None:
        print("\nCould not find a header row this parser recognises.")
        print("Send the block above to whoever maintains aero/avl_runner.py.")
        return 1

    cols = _strip_columns(header)
    print(f"\nparsed column map: {cols}")

    strips = parse_strips(fs)
    tot = sum(s.area for s in strips)
    print(f"\n{len(strips)} strips, sum(area) = {tot:.4f} (deck Sref = 16.0000)")
    wcl = sum(s.cl * s.area for s in strips) / tot
    print(f"area-weighted strip cl = {wcl:.4f}   (should be ~0.60)")
    print("\nfirst 3 strips as parsed:")
    for s in strips[:3]:
        print(f"  j={s.j:3d}  y={s.y:8.4f}  chord={s.chord:7.4f}  area={s.area:7.4f}"
              f"  cl={s.cl:7.4f}  ai={s.ai:7.4f}")

    ok = abs(wcl - 0.6) < 0.02 and abs(tot - 16.0) / 16.0 < 0.03
    print(f"\n{'LOOKS CORRECT' if ok else 'MAPPING IS WRONG -- send this output on'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
