"""Small things that make a stuck process inspectable."""


def enable_stack_dumps() -> None:
    """Let ``kill -USR1`` print every thread's Python stack to stderr.

    A process that stops making progress looks exactly like one doing slow
    work from the outside, and attaching a debugger needs privileges neither
    the container nor a plain user account has. This is the cheap way to tell
    the difference: it cost nothing until the day the worker hung, and then it
    named the deadlock in one signal.
    """
    import faulthandler
    import signal

    faulthandler.enable()
    if hasattr(faulthandler, "register"):
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
