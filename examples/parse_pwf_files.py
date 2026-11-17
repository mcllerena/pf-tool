"""Convert every PWF file in tests/data/op-pwf into tests/data/op-dat."""

from pathlib import Path

from pf_tool.parser import Pwf2DatParser

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PWF_DIRECTORY = REPOSITORY_ROOT / "tests" / "data" / "op-pwf"
DAT_DIRECTORY = REPOSITORY_ROOT / "tests" / "data" / "op-dat"


def main() -> None:
    if not PWF_DIRECTORY.is_dir():
        raise FileNotFoundError(f"PWF input directory does not exist: {PWF_DIRECTORY}")

    pwf_files = sorted(
        (
            path
            for path in PWF_DIRECTORY.iterdir()
            if path.is_file() and path.suffix.casefold() == ".pwf"
        ),
        key=lambda path: path.name.casefold(),
    )
    if not pwf_files:
        raise FileNotFoundError(f"No PWF files found in: {PWF_DIRECTORY}")

    DAT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(pwf_files, start=1):
        destination = DAT_DIRECTORY / f"{source.stem}.dat"
        Pwf2DatParser(source).convert(destination)
        print(f"[{index}/{len(pwf_files)}] {source.name} -> {destination.name}", flush=True)

    print(f"Converted {len(pwf_files)} PWF files to {DAT_DIRECTORY}")


if __name__ == "__main__":
    main()
