# PyInstaller hook for kokoro TTS package.
# Collects all data files (voice configs, model metadata) and submodules
# that PyInstaller's static analysis would otherwise miss.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('kokoro')
hiddenimports = collect_submodules('kokoro')
