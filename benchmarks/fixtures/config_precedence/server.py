import argparse

from config import choose_port


def selected_port(argv, environ):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    return choose_port(args.port, environ)
