"""Audit the three organizer archives without extracting them."""

from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path

from PIL import Image


ID_PATTERN = re.compile(r"(\d+)(?=\.[^.]+$)")


def inspect_archive(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        names = [
            info.filename
            for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith((".tif", ".tiff"))
        ]
        identifiers = []
        for name in names:
            match = ID_PATTERN.search(Path(name).name)
            if match:
                identifiers.append(int(match.group(1)))
        sample_name = names[0] if names else None
        sample_info: dict[str, object] | None = None
        if sample_name is not None:
            with Image.open(io.BytesIO(archive.read(sample_name))) as image:
                sample_info = {
                    "name": sample_name,
                    "size": list(image.size),
                    "mode": image.mode,
                    "frames": image.n_frames,
                }
        return {
            "archive_name": path.name,
            "bad_member": bad_member,
            "tiff_count": len(names),
            "id_min": min(identifiers) if identifiers else None,
            "id_max": max(identifiers) if identifiers else None,
            "unique_id_count": len(set(identifiers)),
            "sample": sample_info,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--optical", type=Path, required=True)
    parser.add_argument("--sar", type=Path, required=True)
    parser.add_argument("--label", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = {
        "optical": inspect_archive(arguments.optical),
        "sar": inspect_archive(arguments.sar),
        "label": inspect_archive(arguments.label),
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with arguments.output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
