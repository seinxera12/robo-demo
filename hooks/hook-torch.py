# PyInstaller hook for PyTorch (CPU-only build).
# torch ships native .dll files on Windows that must be explicitly collected —
# PyInstaller's binary analysis does not walk torch's lib/ directory by default.

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

binaries = collect_dynamic_libs('torch')
datas = collect_data_files('torch')
