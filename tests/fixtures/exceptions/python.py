class Bad(Exception):
    pass


class Other(Exception):
    pass


def observe():
    pass


def cleanup():
    pass


def f():
    cause = RuntimeError()
    try:
        raise Bad() from cause
    except (Bad, Other) as error:
        observe()
        raise
    finally:
        cleanup()
