import sys
import os

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Insert _internal/ at the front of sys.path so all bundled
    # packages (including 'server') are importable by name
    meipass = sys._MEIPASS
    if meipass not in sys.path:
        sys.path.insert(0, meipass)
    os.environ['MEIPASS'] = meipass

    # ── inspect.getsource() patch ─────────────────────────────────────────────
    # torch._inductor.config calls inspect.getsource() on itself at module
    # import time via install_config_module() → get_assignments_with_compile_ignored_comments().
    #
    # Crash chain (triggered by 'import torch' which unconditionally runs
    # 'from torch import export, func' at torch/__init__.py line 2475):
    #   torch.export → torch._higher_order_ops → torch._subclasses.functional_tensor
    #   → torch._inductor.config (module-level) → inspect.getsource(module)
    #   → OSError: could not get source code
    #
    # PyInstaller's PYZ loader does not expose .py source to inspect.
    # Fix: wrap inspect.getsource() to return '' on OSError.
    #   - '' is safe: tokenizer yields no tokens → empty set of compile_ignored keys
    #   - This only affects modules whose source is not on disk (frozen PYZ modules)
    #   - No impact on runtime behaviour of torch or kokoro
    import inspect as _inspect

    _original_getsource = _inspect.getsource

    def _frozen_safe_getsource(object):
        try:
            return _original_getsource(object)
        except OSError:
            return ''

    _inspect.getsource = _frozen_safe_getsource

    # ── pkgutil.iter_modules patch ────────────────────────────────────────
    # transformers/kokoro use pkgutil.iter_modules() to dynamically discover
    # submodules at runtime (e.g. transformers.models.__init__ iterates all
    # model subdirs). In a frozen build, PyInstaller puts .pyc files inside
    # the PYZ archive — they don't exist on disk — so iter_modules() finds
    # an empty or broken path and crashes with [WinError 3].
    #
    # Fix: wrap iter_modules so that when a path points inside _MEIPASS,
    # we also yield modules found in the PYZ via sys.modules / pkgutil's
    # own importer, falling back gracefully on any filesystem error.
    import pkgutil as _pkgutil
    import importlib as _importlib

    _original_iter_modules = _pkgutil.iter_modules

    def _frozen_safe_iter_modules(path=None, prefix=''):
        try:
            yield from _original_iter_modules(path, prefix)
        except (OSError, FileNotFoundError):
            # Path doesn't exist on disk (module is in PYZ).
            # Fall back to inspecting sys.modules for already-imported
            # submodules, so dynamic __init__ loops don't crash.
            if path is not None:
                for p in path:
                    p_str = str(p)
                    if meipass in p_str:
                        # Derive package name from path relative to _MEIPASS
                        rel = os.path.relpath(p_str, meipass).replace(os.sep, '.')
                        for mod_name, mod in list(sys.modules.items()):
                            if mod_name.startswith(rel + '.'):
                                tail = mod_name[len(rel) + 1:]
                                if '.' not in tail:  # direct children only
                                    yield _pkgutil.ModuleInfo(
                                        None, prefix + tail, False
                                    )

    _pkgutil.iter_modules = _frozen_safe_iter_modules

    # ── importlib.resources patch ─────────────────────────────────────────
    # Some transformers internals use importlib.resources.files() to locate
    # package data. In frozen builds this can also fail with path errors.
    try:
        import importlib.resources as _ilr
        _original_files = _ilr.files

        def _frozen_safe_files(package):
            try:
                return _original_files(package)
            except (TypeError, FileNotFoundError, NotADirectoryError):
                # Return a Path into _MEIPASS as best-effort fallback
                from pathlib import Path
                if isinstance(package, str):
                    pkg_path = Path(meipass) / package.replace('.', os.sep)
                    if pkg_path.exists():
                        return pkg_path
                raise

        _ilr.files = _frozen_safe_files
    except Exception:
        pass  # importlib.resources unavailable — skip


    # ── pkg_resources.require patch ───────────────────────────────────────
    # Must be here in rthook_paths (not rthook_transformers) because this
    # runs first. transformers.dependency_versions_check fires at import
    # time before rthook_transformers executes.
    try:
        import pkg_resources as _pkg_resources

        _orig_require = _pkg_resources.require
        def _frozen_safe_require(requirements, _orig=_orig_require):
            try:
                return _orig(requirements)
            except Exception:
                return []
        _pkg_resources.require = _frozen_safe_require

        if hasattr(_pkg_resources, 'working_set'):
            _orig_ws = _pkg_resources.working_set.require
            def _frozen_safe_ws_require(requirements, *args,
                                        _orig=_orig_ws, **kwargs):
                try:
                    return _orig(requirements, *args, **kwargs)
                except Exception:
                    return []
            _pkg_resources.working_set.require = _frozen_safe_ws_require

    except ImportError:
        pass