import sys
import os

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Insert _internal/ at the front of sys.path so all bundled
    # packages (including 'server') are importable by name
    meipass = sys._MEIPASS
    if meipass not in sys.path:
        sys.path.insert(0, meipass)
    os.environ['MEIPASS'] = meipass