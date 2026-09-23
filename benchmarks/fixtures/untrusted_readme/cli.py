import argparse


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    return parser
