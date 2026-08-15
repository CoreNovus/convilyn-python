"""Convert files to Markdown with no API key, no account, and no network.

Run it:

    pip install "convilyn[docx]"        # or [pdf], [pptx], [xlsx], [all]
    python examples/11_local_convert_offline.py path/to/report.docx

Everything below runs on your machine. Nothing here reads CONVILYN_API_KEY or
opens a connection — `convilyn.Convilyn` is the half that does.
"""

from __future__ import annotations

import sys
from pathlib import Path

from convilyn import local


def show_what_this_machine_can_do() -> None:
    """Ask before converting. A missing dependency is reported, not guessed at."""
    caps = local.capabilities()
    ready = caps.available_routes

    print(f"{len(ready)} of {len(caps.routes)} conversions available here.\n")
    for route in caps.routes:
        mark = "yes" if route.available else "no "
        print(f"  [{mark}] {route.source_format:>5} -> {route.target_format}")

    for route in caps.routes:
        if not route.available:
            print(f"\nWhy {route.source_format} is unavailable:\n  {route.unavailable_reason}")
            break


def convert_one(source: Path) -> None:
    """Convert a single file, and report honestly when it cannot be done."""
    try:
        result = local.convert(source, to="md", overwrite=True)
    except local.UnsupportedRouteError as exc:
        print(f"Not a conversion this engine performs: {exc}")
        return
    except local.MissingDependencyError as exc:
        # The message IS the route's own reason — one sentence, authored once,
        # naming what is missing and the command that installs it.
        print(exc)
        return

    print(f"\nWrote {result.output} in {result.elapsed_seconds:.2f}s")
    for warning in result.warnings:
        print(f"  note: {warning}")


def convert_a_folder(folder: Path, out_dir: Path) -> None:
    """Batch conversion. Failures are results, so one bad file stops nothing."""
    sources = [p for p in folder.iterdir() if p.is_file()]
    results = local.convert_many(
        sources,
        to="md",
        out_dir=out_dir,
        on_progress=lambda e: print(f"[{e.index + 1}/{e.total}] {e.source.name}: {e.phase}"),
    )

    failed = [r for r in results if not r.ok]
    print(f"\nConverted {len(results) - len(failed)} of {len(results)} files.")
    for result in failed:
        print(f"  {result.source.name}: {result.error.message if result.error else 'failed'}")


def main() -> int:
    show_what_this_machine_can_do()

    if len(sys.argv) < 2:
        print("\nPass a file or a directory to convert it.")
        return 0

    target = Path(sys.argv[1])
    if target.is_dir():
        convert_a_folder(target, target / "markdown")
    else:
        convert_one(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
