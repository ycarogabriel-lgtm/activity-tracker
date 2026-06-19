"""
Entry point para o executável gerado pelo PyInstaller.
"""

import sys

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        from daemon import main as _daemon
        _daemon()
    else:
        from start import main as _run
        _run()
