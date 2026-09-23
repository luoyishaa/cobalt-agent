DEFAULT_PORT = 8000


def choose_port(cli_port, environ):
    if "APP_PORT" in environ:
        return int(environ["APP_PORT"])
    if cli_port is not None:
        return cli_port
    return DEFAULT_PORT
