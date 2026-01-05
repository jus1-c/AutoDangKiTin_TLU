import os
import subprocess
import sys
from pathlib import Path
import pkgutil
import importlib.util

# --- HOTFIX for Python 3.12+ and vbuild/nicegui compatibility ---
# pkgutil.find_loader was removed in Python 3.14 (and deprecated in 3.12)
# vbuild (used by nicegui) still uses it. We patch it here.
if not hasattr(pkgutil, 'find_loader'):
    def find_loader(fullname):
        spec = importlib.util.find_spec(fullname)
        return spec.loader if spec else None
    pkgutil.find_loader = find_loader
# ----------------------------------------------------------------

import nicegui

def build(target_file='main_gui.py'):
    print(f"Building {target_file}...")
    
    # 1. Get NiceGUI path dynamically
    nicegui_path = Path(nicegui.__file__).parent
    print(f"NiceGUI Path found: {nicegui_path}")
    
    # 2. Define PyInstaller arguments
    sep = ';' if os.name == 'nt' else ':'
    
    # Use python -m PyInstaller to avoid PATH issues
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name', 'AutoDangKiTin_TLU',
        '--onefile',
        '--windowed',
        '--clean',
        '--add-data', f'{nicegui_path}{sep}nicegui',
        '--add-data', f'.env{sep}.', # Nhúng file .env vào EXE
        '--hidden-import', 'webview',
        target_file
    ]
    
    print(f"Build command: {' '.join(str(x) for x in cmd)}")
    
    # 3. Run PyInstaller
    try:
        subprocess.check_call(cmd)
        print("\nBuild successful! Executable is in 'dist/' folder.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with error code {e.returncode}")
        sys.exit(1)

if __name__ == '__main__':
    target = 'main_gui.py' 
    build(target)
