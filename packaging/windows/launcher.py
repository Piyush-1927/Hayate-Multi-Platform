import sys


def _child_mode():
    """Frozen builds: sys.executable is Hayate.exe, so 'python -m x' / 'python -c x' child
    processes (e.g. the comments window) must be run here instead of starting the app again."""
    a = sys.argv
    if len(a) >= 3 and a[1] == "-m":
        import runpy
        sys.argv = [a[2]] + a[3:]
        runpy.run_module(a[2], run_name="__main__", alter_sys=True)
        return True
    if len(a) >= 3 and a[1] == "-c":
        sys.argv = ["-c"] + a[3:]
        exec(a[2], {"__name__": "__main__"})
        return True
    return False


if __name__ == "__main__":
    if _child_mode():
        sys.exit(0)
    try:
        from hayate.main import main
    except ImportError:
        main = None
    if main is not None:
        sys.exit(main())
    import runpy
    runpy.run_module("hayate.main", run_name="__main__")
