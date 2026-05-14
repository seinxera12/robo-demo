# hooks/rthook_torch.py
# Force torch to fully initialize before any app code runs.
# Prevents "partially initialized module 'torch'" circular import errors
# that occur in frozen builds when kokoro/TTS triggers a second torch import.

import sys

# Only apply in frozen builds
if getattr(sys, 'frozen', False):
    import torch
    import torch.version
    import torch.jit
    import torch.nn
    import torch.nn.functional
    # Touch torch.version explicitly — this is the attribute that fails
    _ = torch.version.__version__