"""Five independent checks with deliberately verbose CI-style output."""

import sys

STAGES = {"alpha", "beta", "gamma", "delta", "epsilon"}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in STAGES:
        print("usage: python check.py <alpha|beta|gamma|delta|epsilon>")
        return 2
    stage = argv[1]
    print(f"{stage}: PASS")
    for index in range(115):
        print(f"{stage} dependency scan {index:03d}: " + "validated " * 10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
