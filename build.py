import os
import subprocess
import nicegui
from pathlib import Path

def build():
    # 1. Get NiceGUI path to include assets
    nicegui_path = Path(nicegui.__file__).parent
    
    # 2. Define PyInstaller arguments
    cmd = [
        'pyinstaller',
        '--name', 'AutoDangKiTin_TLU', # Tên file exe
        '--onefile',                   # Đóng gói thành 1 file duy nhất
        '--windowed',                  # Không hiện cửa sổ đen (console) khi chạy (Optional)
        # Note: Nếu bạn muốn xem log debug thì bỏ dòng '--windowed' đi
        
        '--add-data', f'{nicegui_path}{os.pathsep}nicegui', # Copy thư viện NiceGUI vào
        'main_gui.py'                  # File chạy chính
    ]
    
    print(f"Build command: {' '.join(str(x) for x in cmd)}")
    
    # 3. Run
    subprocess.call(cmd)

if __name__ == '__main__':
    build()
