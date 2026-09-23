import argparse


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return f"Listening on port {args.port}"
