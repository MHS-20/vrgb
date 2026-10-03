"""VRGB Suite: access to VRGB Core (the `vrgb` module / CLI)."""

import importlib.machinery
import importlib.util
from pathlib import Path



# System locations of the CLI: distro package first, then ./install.sh.
SYSTEM_CLI_PATHS = (Path("/usr/bin/vrgb"), Path("/usr/local/bin/vrgb"))


def _core_candidates():
    repo = Path(__file__).resolve().parents[2]
    return [
        *SYSTEM_CLI_PATHS,             # ./install.sh copies the single file here
        repo / "vrgb.py",              # running from the repo checkout
    ]


def load_core():
    """Return (module, origin). Packaged installs provide `import vrgb`;
    otherwise load the single-file CLI by path."""
    try:
        import vrgb
        if hasattr(vrgb, "find_device"):
            return vrgb, Path(vrgb.__file__)
    except ImportError:
        pass
    for path in _core_candidates():
        if path.exists():
            loader = importlib.machinery.SourceFileLoader("vrgb_core", str(path))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)   # main() is guarded by __name__ == "__main__"
            return mod, path
    raise FileNotFoundError(
        "Could not find VRGB Core. Install it (package or ./install.sh) or run the GUI "
        "from the repository checkout next to vrgb.py."
    )


def pkexec_target():
    """A SAFE root-owned binary to run under pkexec, or None.

    Running a user-writable file as root is a local privilege-escalation primitive,
    so we require a system copy of the CLI that is owned by root and not group- or
    world-writable. We never fall back to the (user-owned) repo checkout.
    """
    for p in SYSTEM_CLI_PATHS:
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_uid != 0:
            continue
        if st.st_mode & 0o022:      # group/other writable -> unsafe
            continue
        return str(p)
    return None

