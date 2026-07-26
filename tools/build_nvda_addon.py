#!/usr/bin/env python3
"""Build the Modern Mono NVDA add-on from the staged addon directory."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "addon"
OUTPUT = ROOT / "dist" / "modernMono-0.6.13.nvda-addon"


def main() -> None:
	OUTPUT.parent.mkdir(exist_ok=True)
	with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
		for path in sorted(SOURCE.rglob("*")):
			if path.is_file() and "__pycache__" not in path.parts:
				archive.write(path, path.relative_to(SOURCE).as_posix())
	print(OUTPUT)


if __name__ == "__main__":
	main()
