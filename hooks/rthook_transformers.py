# rthook_transformers.py

import sys
import os

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    meipass = sys._MEIPASS

    # ── importlib.util.spec_from_file_location patch ──────────────────────
    import importlib.util as _ilu
    _orig_spec_from_file = _ilu.spec_from_file_location

    def _frozen_spec_from_file_location(name, location=None, *args,
                                         _meipass=meipass,
                                         _orig=_orig_spec_from_file,
                                         **kwargs):
        if location is not None:
            loc_str = str(location)
            if loc_str.endswith('.pyc') and not os.path.exists(loc_str):
                py_path = loc_str[:-1]
                if os.path.exists(py_path):
                    location = py_path
                else:
                    return _orig(name, None, *args, **kwargs)
        return _orig(name, location, *args, **kwargs)

    _ilu.spec_from_file_location = _frozen_spec_from_file_location

    # ── builtins.open patch ───────────────────────────────────────────────
    import builtins
    _builtin_open = builtins.open

    def _frozen_safe_open(file, *args,
                          _meipass=meipass,
                          _orig_open=_builtin_open,
                          **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            file_str = os.fsdecode(file) if isinstance(file, bytes) else str(file)
            if (file_str.endswith('.pyc') and
                    _meipass in file_str and
                    not os.path.exists(file_str)):
                py_path = file_str[:-1]
                if os.path.exists(py_path):
                    return _orig_open(py_path, *args, **kwargs)
        return _orig_open(file, *args, **kwargs)

    builtins.open = _frozen_safe_open

    # ── pkg_resources.require patch ───────────────────────────────────────
    try:
        import pkg_resources as _pkg_resources
        _orig_require = _pkg_resources.require

        def _frozen_safe_require(requirements, _orig=_orig_require):
            try:
                return _orig(requirements)
            except (_pkg_resources.DistributionNotFound,
                    _pkg_resources.VersionConflict,
                    Exception):
                return []

        _pkg_resources.require = _frozen_safe_require

        if hasattr(_pkg_resources, 'working_set'):
            _orig_ws_require = _pkg_resources.working_set.require

            def _frozen_safe_ws_require(requirements, *args,
                                        _orig=_orig_ws_require, **kwargs):
                try:
                    return _orig(requirements, *args, **kwargs)
                except Exception:
                    return []

            _pkg_resources.working_set.require = _frozen_safe_ws_require

    except ImportError:
        pass