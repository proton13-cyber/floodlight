"""
Agent-safe driver for Mark Drela's AVL.

AVL is an interactive, prompt-driven Fortran program. It was never designed to be
called by a program, let alone by an LLM agent, and it fails in ways that look like
success. This module exists so that no agent ever pipes raw text at AVL again.

The four failure modes this module is built around -- each one reproduced against a
real AVL build, see aero/AGENTS.md for the transcripts:

  1. STREAM DESYNC. If an output file already exists, AVL interrupts the command
     stream with "File exists.  Append/Overwrite/Cancel  (A/O/C)?" and eats the next
     line of stdin as the answer. Every subsequent command is now shifted by one and
     AVL executes a different case than the one you asked for -- usually without any
     error. Defence: every run happens in a fresh scratch directory.

  2. EXIT CODE 0 IS NOT SUCCESS. A geometry file that fails to load prints
     "** File not processed. Current geometry may be corrupted." and AVL keeps going
     and exits 0. Defence: success is defined as "the forces file exists and parses",
     never as "returncode == 0".

  3. PLOTTING ABORTS. Any plot command (G, T, ...) opens an X11 window. With no
     display -- i.e. every headless agent context -- AVL prints
     "Cannot open display...aborting" and dies mid-stream. Defence: the command
     builder here cannot emit a plot command, and DISPLAY is stripped from the child
     environment so a stray one fails loudly and instantly instead of hanging.

  4. EOF IS A CRASH. Reaching end-of-stdin at a prompt raises a Fortran runtime
     error and exits 2. Defence: every command list is terminated with enough QUITs
     to unwind to the top level and exit cleanly.

  5. NaN IS PRINTED, NOT RAISED. On a dense cosine-spaced spanwise lattice, a
     single-precision AVL build writes "CLtot = NaN" into the forces file while the
     Trefftz-plane block right below it (CLff, CDff, e) still holds correct-looking
     numbers. Exit code 0, no warning anywhere. Measured on this build: cosine
     spacing is clean to 32 spanwise stations per semispan and NaN from 48 up.
     Defence: the parser rejects any non-finite field explicitly, and the deck
     generator caps station counts.

Plus the ambient one: a subprocess with no timeout is a hung MDAO campaign. Every
call here runs under a hard wall-clock timeout and is killed by process group.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "AvlError",
    "AvlNotFound",
    "AvlTimeout",
    "AvlLoadFailed",
    "AvlNoOutput",
    "AvlNumericalFailure",
    "AvlResult",
    "find_avl",
    "run_case",
]


# --------------------------------------------------------------------------------------
# errors -- an agent should be able to branch on the type, not regex a log
# --------------------------------------------------------------------------------------

class AvlError(RuntimeError):
    """Base class. Carries the AVL stdout log so a diagnosing agent has something to read."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


class AvlNotFound(AvlError):
    """No AVL executable could be located."""


class AvlTimeout(AvlError):
    """AVL did not exit within the wall-clock budget; it was killed."""


class AvlLoadFailed(AvlError):
    """AVL refused the geometry (or mass/run) file. It would have exited 0 anyway."""


class AvlNoOutput(AvlError):
    """AVL exited but wrote no parseable forces file. The run produced nothing."""


class AvlNumericalFailure(AvlError):
    """AVL wrote NaN or Inf into the forces file and exited 0 anyway.

    Almost always a lattice problem: too many cosine-spaced spanwise stations for a
    single-precision build, so the innermost/outermost strips collapse together.
    Reduce n_span, or switch the spanwise distribution to sine (-1.0) or equal (0.0).
    """


# --------------------------------------------------------------------------------------
# locating the binary
# --------------------------------------------------------------------------------------

def find_avl(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate an AVL executable.

    Order: explicit argument, $AVL_BIN, then PATH ("avl", "avl.exe", "avl352.exe").

    NOTE FOR AGENTS: a Windows avl*.exe cannot be executed from a Linux container or
    from the Cowork local VM. If you are on Linux you need a Linux build; see
    aero/AGENTS.md, "Getting a binary you can actually run".
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("AVL_BIN")
    if env:
        candidates.append(Path(env))
    for name in ("avl", "avl.exe", "avl352.exe", "avl3.36.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c.resolve()
        if c.is_file():  # exists but not executable -- most likely a .exe on Linux
            raise AvlNotFound(
                f"{c} exists but is not executable here. If this is a Windows .exe on "
                f"Linux, it cannot be run: build or install a Linux AVL instead."
            )

    raise AvlNotFound(
        "No AVL executable found. Set AVL_BIN to its full path, or put 'avl' on PATH."
    )


# --------------------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------------------

@dataclass
class Strip:
    """One spanwise strip from AVL's FS output.

    `cl` is referred to the strip's own chord, so it is directly the local section
    lift coefficient a 2-D method needs. `ai` is the induced angle at the strip.
    """

    j: int
    y: float
    chord: float
    area: float
    cl: float
    ai: float
    cd_induced: float
    cm_c4: float


@dataclass
class AvlResult:
    """Parsed totals from one converged AVL run case."""

    alpha_deg: float
    CL: float
    CD: float          # CDtot: induced + the viscous CD given in the deck
    CD_induced: float
    CD_viscous: float
    Cm: float
    e: float           # Trefftz-plane span efficiency
    CL_trefftz: float
    CD_trefftz: float
    Sref: float
    Bref: float
    Cref: float
    forces_path: Path
    log: str = field(repr=False, default="")
    strips: list[Strip] = field(repr=False, default_factory=list)

    @property
    def LD(self) -> float:
        return self.CL / self.CD if self.CD > 0 else float("nan")

    def as_dict(self) -> dict:
        d = {
            k: v for k, v in self.__dict__.items()
            if k not in ("log", "forces_path", "strips")
        }
        d["forces_path"] = str(self.forces_path)
        d["LD"] = self.LD
        return d


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------

_NUM = r"([-+]?\d*\.?\d+(?:[EeDd][-+]?\d+)?)"


def _scalar(text: str, label: str) -> float | None:
    """Pull `label = value` out of an AVL .ft block. AVL pads inconsistently, so this
    is deliberately whitespace-tolerant and anchored on the '=' rather than columns."""
    m = re.search(re.escape(label) + r"\s*=\s*" + _NUM, text)
    if not m:
        return None
    return float(m.group(1).replace("D", "E").replace("d", "e"))


def parse_forces(path: str | os.PathLike[str]) -> dict:
    """Parse an AVL 'FT' (total forces) file into a flat dict.

    Raises AvlNoOutput if the file is absent or does not contain the totals block --
    which is exactly what happens when the stream desynced or the geometry failed to
    load, so this doubles as the run's success test.
    """
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        raise AvlNoOutput(f"AVL wrote no forces file at {p}")
    text = p.read_text(errors="replace")

    if "Vortex Lattice Output" not in text:
        raise AvlNoOutput(f"{p} is not an AVL total-forces file (stream desync?)")

    # Catch the silent-NaN case before field extraction, so the error names the real
    # cause instead of reporting fields as "missing" (the regex simply won't match
    # "NaN"). AVL emits these with exit code 0 and no warning.
    bad = sorted(set(re.findall(r"\b(NaN|nan|-?Inf(?:inity)?|\*{3,})\b", text)))
    if bad:
        raise AvlNumericalFailure(
            f"{p} contains non-finite values {bad}. The solve did not converge -- "
            f"usually too many cosine-spaced spanwise stations for a single-precision "
            f"AVL build. Reduce n_span (<=32/semispan is safe) or use sine spacing."
        )

    out: dict[str, float | None] = {}
    for key, label in (
        ("alpha_deg", "Alpha"),
        ("CL", "CLtot"),
        ("CD", "CDtot"),
        ("CD_viscous", "CDvis"),
        ("CD_induced", "CDind"),
        ("Cm", "Cmtot"),
        ("CL_trefftz", "CLff"),
        ("CD_trefftz", "CDff"),
        ("e", "e"),
        ("Sref", "Sref"),
        ("Bref", "Bref"),
        ("Cref", "Cref"),
    ):
        out[key] = _scalar(text, label)

    missing = [k for k, v in out.items() if v is None]
    if missing:
        raise AvlNoOutput(f"{p} is missing expected fields: {missing}")
    return out  # type: ignore[return-value]


# --------------------------------------------------------------------------------------
# the runner
# --------------------------------------------------------------------------------------

def parse_strips(path: str | os.PathLike[str]) -> list[Strip]:
    """Parse AVL's FS (strip forces) file.

    The table we want is under 'Strip Forces referred to Strip Area, Chord':

        j   Yle   Chord   Area   c cl   ai   cl_norm   cl   cd   cdv   cm_c/4 ...

    Two traps. First, the file repeats the whole block for the YDUPLICATE half, so a
    symmetric wing yields both sides -- keep them, the areas are per-strip and summing
    all of them gives the full wing area, but do not also add the header's Sref.
    Second, `cl_norm` and `cl` are adjacent and easy to swap: `cl` (column 8) is the
    one referred to the strip chord, and it is the one a 2-D section method wants.
    """
    p = Path(path)
    if not p.is_file():
        raise AvlNoOutput(f"AVL wrote no strip-force file at {p}")
    text = p.read_text(errors="replace")
    if "Strip Forces referred to" not in text:
        raise AvlNoOutput(f"{p} has no strip-force table (stream desync?)")
    if re.search(r"\b(NaN|nan|-?Inf)\b", text):
        raise AvlNumericalFailure(f"{p} contains non-finite strip values")

    strips: list[Strip] = []
    for block in text.split("Strip Forces referred to Strip Area, Chord")[1:]:
        started = False  # per-block: the first blank AFTER rows ends this table.
        # (Tracking this globally silently drops every block but the first, because
        #  each split leaves a leading empty line -- which is how the YDUPLICATE half
        #  of a symmetric wing goes missing and the wing area comes out halved.)
        cols: dict[str, int] | None = None
        for line in block.splitlines():
            if cols is None:
                cols = _strip_columns(line)
                if cols is not None:
                    continue
            f = line.split()
            if len(f) < 10 or not f[0].isdigit():
                if started and not f:
                    break
                continue
            started = True
            try:
                vals = [float(x) for x in f]
            except ValueError:
                continue
            try:
                strips.append(
                    Strip(
                        j=int(f[0]),
                        y=vals[cols["Yle"]],
                        chord=vals[cols["Chord"]],
                        area=vals[cols["Area"]],
                        ai=vals[cols["ai"]],
                        cl=vals[cols["cl"]],
                        cd_induced=vals[cols["cd"]],
                        cm_c4=vals[cols.get("cm_c/4", cols["cd"])],
                    )
                )
            except (KeyError, IndexError) as exc:
                raise AvlNoOutput(
                    f"{p}: strip table columns did not match. Missing/short: {exc}. "
                    f"Header was: {cols!r}. This usually means a different AVL "
                    f"version reordered or added a column."
                ) from None
    if not strips:
        raise AvlNoOutput(f"{p} strip table parsed to zero rows")
    return strips


def _strip_columns(line: str) -> dict[str, int] | None:
    """Map FS column names to field indices, from the table's own header row.

    Read this by NAME, never by fixed position. AVL versions differ in what the strip
    table carries, and a one-column shift is silent and catastrophic: `cl` lands on
    `cdv`, which is 0.0000 on every row, so every strip reports zero lift. Downstream
    that surfaces as "no strips large enough to define CL_max" -- an error that points
    nowhere near the actual cause.

    The header needs one fixup before splitting: the column labelled `c cl` contains a
    space, so a naive split would yield one more token than there are columns and shift
    everything after it by one.
    """
    if "Chord" not in line or "Area" not in line:
        return None
    toks = line.replace("c cl", "c_cl").split()
    if not toks or toks[0] != "j":
        return None
    cols = {name: i for i, name in enumerate(toks)}
    for required in ("Yle", "Chord", "Area", "ai", "cl", "cd"):
        if required not in cols:
            return None
    return cols


def _build_commands(
    geometry_name: str,
    *,
    mass_name: str | None,
    trim: tuple[str, float],
    forces_name: str,
    strips_name: str | None = None,
    extra_oper: Sequence[str] = (),
) -> list[str]:
    """Assemble the stdin script.

    This function is the only place AVL commands are authored. It refuses to emit a
    plot command, and it always terminates the stream with explicit QUITs.
    """
    mode, value = trim
    if mode not in ("alpha", "CL"):
        raise ValueError("trim mode must be 'alpha' or 'CL'")

    cmds: list[str] = []
    if mass_name:
        cmds.append(f"MASS {mass_name}")
        cmds.append("MSET")
        cmds.append("0")          # apply the mass file to all run cases
    cmds.append(f"LOAD {geometry_name}")
    cmds.append("OPER")
    # 'A' selects the alpha constraint; second token is what alpha is set BY.
    cmds.append(f"A A {value:.6f}" if mode == "alpha" else f"A C {value:.6f}")
    for c in extra_oper:
        if re.match(r"^\s*(G|T|MOVIE|H)\b", c, re.I):
            raise ValueError(f"refusing plot/hardcopy command in headless run: {c!r}")
        cmds.append(c)
    cmds.append("X")                    # execute
    cmds.append(f"FT {forces_name}")    # totals to file -- never scrape stdout
    if strips_name:
        cmds.append(f"FS {strips_name}")
    # A BLANK LINE IS NOT A NO-OP. In AVL a blank line means "go up one menu level",
    # and it is the ONLY way out of OPER -- QUIT is not an OPER command, it is only
    # recognised at the top level. So the exit is: blank (leave OPER), QUIT (leave AVL).
    # Get this wrong and the symptom is remote from the cause: a stray blank after FT
    # drops you to the top level, the following FS is rejected as "command not
    # recognized", no strip file is ever written, and AVL still exits 0.
    cmds.append("")                     # leave OPER
    cmds.append("QUIT")                 # leave AVL -- never let it hit EOF
    return cmds


def run_case(
    geometry: str | os.PathLike[str],
    *,
    trim: tuple[str, float] = ("alpha", 0.0),
    mass_file: str | os.PathLike[str] | None = None,
    aux_files: Iterable[str | os.PathLike[str]] = (),
    avl_bin: str | os.PathLike[str] | None = None,
    timeout_s: float = 120.0,
    keep_dir: str | os.PathLike[str] | None = None,
    want_strips: bool = False,
    extra_oper: Sequence[str] = (),
) -> AvlResult:
    """Run one AVL case and return parsed totals.

    Every call gets a fresh scratch directory containing copies of the geometry and any
    aux files (airfoil .dat coordinates, mass file). Nothing is ever written next to the
    caller's inputs, and no output file ever pre-exists -- which is what makes the
    overwrite prompt, and therefore the stream desync, unreachable.

    Args:
        geometry:  path to the .avl deck.
        trim:      ("alpha", degrees) or ("CL", target_CL).
        mass_file: optional .mass file, applied to all run cases.
        aux_files: airfoil coordinate files etc. referenced by the deck.
        timeout_s: hard wall-clock budget. Exceeding it kills the process group.
        keep_dir:  if given, the scratch directory is created here and NOT deleted --
                   use this when an agent needs to inspect a failed run.

    Raises:
        AvlNotFound, AvlTimeout, AvlLoadFailed, AvlNoOutput -- all subclasses of AvlError.
    """
    exe = find_avl(avl_bin)
    geometry = Path(geometry).resolve()
    if not geometry.is_file():
        raise AvlLoadFailed(f"geometry file does not exist: {geometry}")

    if keep_dir is not None:
        work = Path(tempfile.mkdtemp(prefix="avl_", dir=str(keep_dir)))
        cleanup = False
    else:
        work = Path(tempfile.mkdtemp(prefix="avl_"))
        cleanup = True

    try:
        shutil.copy2(geometry, work / geometry.name)
        for f in aux_files:
            f = Path(f)
            if f.is_file():
                shutil.copy2(f, work / f.name)
        mass_name = None
        if mass_file:
            mf = Path(mass_file).resolve()
            if not mf.is_file():
                raise AvlLoadFailed(f"mass file does not exist: {mf}")
            shutil.copy2(mf, work / mf.name)
            mass_name = mf.name

        forces_name = "totals.ft"
        strips_name = "strips.fs" if want_strips else None
        cmds = _build_commands(
            geometry.name,
            mass_name=mass_name,
            trim=trim,
            forces_name=forces_name,
            strips_name=strips_name,
            extra_oper=extra_oper,
        )
        stdin_text = "\n".join(cmds) + "\n"

        # DISPLAY is stripped so a stray plot command aborts immediately and visibly
        # rather than opening a window or blocking on an X connection.
        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}

        popen_kwargs: dict = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True  # own process group -> killable
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(work),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            **popen_kwargs,
        )
        try:
            log, _ = proc.communicate(stdin_text, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill(proc)
            log, _ = proc.communicate()
            raise AvlTimeout(
                f"AVL exceeded {timeout_s:.0f}s and was killed. Scratch dir: {work}",
                log or "",
            )

        if "File not processed" in log or "Open error on file" in log:
            raise AvlLoadFailed(
                f"AVL could not read an input file (it still exited "
                f"{proc.returncode}). Scratch dir: {work}",
                log,
            )
        if "Cannot open display" in log:
            raise AvlError("A plot command reached AVL in a headless run.", log)

        try:
            parsed = parse_forces(work / forces_name)
        except AvlNoOutput as exc:
            raise AvlNoOutput(f"{exc} (AVL returncode={proc.returncode})", log) from None

        strips = parse_strips(work / strips_name) if strips_name else []

        forces_path = work / forces_name
        if cleanup:
            # keep the forces file for provenance even after the scratch dir goes away
            persisted = Path(tempfile.gettempdir()) / f"avl_last_totals.ft"
            shutil.copy2(forces_path, persisted)
            forces_path = persisted

        return AvlResult(forces_path=forces_path, log=log, strips=strips, **parsed)

    finally:
        if cleanup and work.exists():
            shutil.rmtree(work, ignore_errors=True)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
