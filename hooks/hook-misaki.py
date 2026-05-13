# PyInstaller hook for misaki G2P (grapheme-to-phoneme) package.
# misaki ships language data files under misaki/data/ that must be bundled,
# and has several optional language backends imported at runtime.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files('misaki')
hiddenimports = collect_submodules('misaki')
