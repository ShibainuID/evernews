"""T27 index builder CLI: precompute demo source hashes into ``_index.json``.

Reads each ``data/demo_sources/src_*/metadata.json`` plus ``frame_*.jpg``,
writes the generated ``data/demo_sources/_index.json`` (gitignored, never
committed). Sources without ``metadata.json`` are skipped with a warning.
Output is deterministic: sorted sources/frames, ``sort_keys`` JSON, no
timestamps or absolute paths.
"""

import argparse
import json
import warnings
from pathlib import Path

from backend.utils.visual_hash import frame_average_hash

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROOT = _REPO_ROOT / "data" / "demo_sources"
_DEFAULT_INDEX = _DEFAULT_ROOT / "_index.json"


def build_index(demo_root: Path, index_path: Path) -> list[dict]:
    """Hash every committed demo source into ``index_path``; return the entries."""
    entries = []
    for source_dir in sorted(p for p in demo_root.glob("src_*") if p.is_dir()):
        metadata_path = source_dir / "metadata.json"
        if not metadata_path.is_file():
            warnings.warn(
                f"skipping {source_dir.name}: metadata.json missing", UserWarning, stacklevel=2
            )
            continue
        metadata = json.loads(metadata_path.read_text())
        frame_hashes = {
            frame_path.name: frame_average_hash(frame_path)
            for frame_path in sorted(source_dir.glob("frame_*.jpg"))
        }
        entries.append({"source_id": source_dir.name, "metadata": metadata, "frame_hashes": frame_hashes})
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({"version": 1, "sources": entries}, indent=2, sort_keys=True) + "\n")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the demo source index (data/demo_sources/_index.json).")
    parser.add_argument("--root", default=str(_DEFAULT_ROOT), help="demo sources directory (default: %(default)s)")
    parser.add_argument("--index", default=str(_DEFAULT_INDEX), help="output index path (default: %(default)s)")
    args = parser.parse_args()
    entries = build_index(Path(args.root), Path(args.index))
    print(f"wrote {args.index} with {len(entries)} sources")


if __name__ == "__main__":
    main()
