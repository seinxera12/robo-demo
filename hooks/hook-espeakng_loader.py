# PyInstaller hook for espeakng_loader.
# Ships espeak-ng-data/ (phoneme dictionaries) and espeak-ng.dll.
# Required by misaki's English G2P backend.

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files('espeakng_loader')
binaries = collect_dynamic_libs('espeakng_loader')
