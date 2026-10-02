#!/usr/bin/env python3
"""List the files in each project's uploads folder so the site knows what to load.

GitHub Pages cannot list a folder, so this writes data/<slug>/files.json with
the names of the data files in data/<slug>/uploads/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_TYPES = {".csv", ".tsv", ".json", ".jsonl", ".ndjson"}


def build(root=ROOT):
    written = []
    data = root / "data"
    if not data.is_dir():
        return written
    for project in sorted(p for p in data.iterdir() if p.is_dir()):
        uploads = project / "uploads"
        files = []
        if uploads.is_dir():
            files = [{"name": f.name, "bytes": f.stat().st_size}
                     for f in sorted(uploads.iterdir(), key=lambda f: f.name.lower())
                     if f.is_file() and f.suffix.lower() in DATA_TYPES and not f.name.startswith(".")]
        target = project / "files.json"
        text = json.dumps({"files": files}, indent=2) + "\n"
        if files or target.exists():
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                target.write_text(text, encoding="utf-8")
            written.append((project.name, len(files)))
    return written


if __name__ == "__main__":
    for slug, count in build():
        print(f"{slug}: {count} file(s) in uploads")
    sys.exit(0)
