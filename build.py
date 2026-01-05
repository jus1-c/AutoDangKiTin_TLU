import os
import subprocess
import sys
import nicegui
from pathlib import Path

def build(target_file='main_gui.py'):
    print(f"Building {target_file}...")
    
    # 1. Get NiceGUI path dynamically to include assets
    nicegui_path = Path(nicegui.__file__).parent
    print(f"NiceGUI Path found: {nicegui_path}")
    
    # 2. Define PyInstaller arguments
    # Separator: ';' for Windows, ':' for Linux/Unix
    sep = ';' if os.name == 'nt' else ':'
    
    cmd = [
        'pyinstaller',
        '--name', 'AutoDangKiTin_TLU', # Name of the executable
        '--onefile',                   # Bundle into a single file
        # '--windowed',                # Uncomment to hide console window (GUI mode)
        '--clean',                     # Clean cache
        '--add-data', f'{nicegui_path}{sep}nicegui', # Include NiceGUI assets
        target_file                    # Main entry script
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
    # You can change this to 'main_web.py' if you want to build the multi-user version
    target = 'main_gui.py' 
    build(target)