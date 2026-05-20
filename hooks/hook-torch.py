# PyInstaller hook for PyTorch (CPU-only build).
# Collects native .dll files that PyInstaller's binary analysis does not walk
# by default (torch ships them under torch/lib/).
#
# NOTE: torch data files (e.g. share/cmake, version.txt) are NOT collected here
# because the spec file collects torch binaries explicitly via torch_binaries.
# Collecting datas here too would double-copy those files and risk path conflicts.

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs('torch')
