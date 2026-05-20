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