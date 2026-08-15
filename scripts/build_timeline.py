"""
Generates a build timeline from filesystem modification times, not git log
-- everything in this session is landing as one commit, so git history
can't reconstruct the actual sequence of work. mtimes can.

Usage:
    python scripts/build_timeline.py [output_path]   # default: docs/BUILD_TIMELINE.md
"""
import io
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent

EXCLUDE_DIRS = {
    '.git', '__pycache__', 'node_modules', '.venv', 'venv', '.pytest_cache',
    '.hf_cache', '.kokoro_models', '.mediapipe_models', 'dist', 'build',
    '.pipcache', '.tmp', 'sample_images', 'results', 'backups', '.claude',
}
EXCLUDE_SUFFIXES = {'.pyc', '.pyo', '.log', '.sql', '.gz', '.env'}
EXCLUDE_NAMES = {'database_backup.sql', '.coverage', '.env.example'}

# Group files whose mtimes fall within this many seconds of each other into
# one timeline entry -- otherwise every file in a single multi-file edit
# would print as its own separate line.
CLUSTER_GAP_SECONDS = 180


def collect_files():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith('.')]
        for fname in filenames:
            if fname in EXCLUDE_NAMES:
                continue
            if any(fname.endswith(s) for s in EXCLUDE_SUFFIXES):
                continue
            fpath = Path(dirpath) / fname
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                continue
            files.append((mtime, fpath.relative_to(ROOT)))
    return sorted(files)


def cluster(files):
    clusters = []
    current = []
    for mtime, path in files:
        if current and mtime - current[-1][0] > CLUSTER_GAP_SECONDS:
            clusters.append(current)
            current = []
        current.append((mtime, path))
    if current:
        clusters.append(current)
    return clusters


def main():
    out_path = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "docs/BUILD_TIMELINE.md")
    files = collect_files()
    if not files:
        print("No files found.", file=sys.stderr)
        return
    clusters = cluster(files)

    lines = []
    lines.append("# Scenarai — Build Timeline")
    lines.append("")
    lines.append(f"Generated from filesystem modification times ({len(files)} files, "
                  f"{len(clusters)} clustered edit groups) — git history collapses this "
                  f"session into a single commit, so mtimes are the only real record "
                  f"of sequence.")
    lines.append("")

    current_day = None
    for c in clusters:
        ts = datetime.fromtimestamp(c[0][0])
        day = ts.strftime('%Y-%m-%d')
        if day != current_day:
            lines.append(f"\n## {day}\n")
            current_day = day
        time_range = ts.strftime('%H:%M')
        last_ts = datetime.fromtimestamp(c[-1][0])
        if last_ts.strftime('%H:%M') != time_range:
            time_range += f"–{last_ts.strftime('%H:%M')}"
        paths = [str(p).replace('\\', '/') for _, p in c]
        if len(paths) == 1:
            lines.append(f"- **{time_range}** — `{paths[0]}`")
        else:
            lines.append(f"- **{time_range}** — {len(paths)} files:")
            for p in paths:
                lines.append(f"  - `{p}`")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out_path} ({len(clusters)} entries)", file=sys.stderr)


if __name__ == "__main__":
    main()
