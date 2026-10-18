import sys

if __name__ == "__main__":
    try:
        from hayate.main import main
    except ImportError:
        main = None
    if main is not None:
        sys.exit(main())
    import runpy
    runpy.run_module("hayate.main", run_name="__main__")
