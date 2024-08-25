# AutoDangKiTin_TLU

Không gì khác ngoài 1 project nho nhỏ của 1 thằng sv năm 2 khoa CNTT

Các bước hướng dẫn dưới đây có thể dùng được cho cả android (termux) và các thiết bị chạy Windows/Linux/MacOS

Script yêu cầu python (hiển nhiên rồi, cái này được viết bằng python mà :v) và 1 số thư viện cài qua lệnh pip:

```sh
  pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib \
  pip install httpx 
```
Cài đặt thêm git để clone source:
Với các thiết bị Windows thì nếu không muốn clone source bạn có thể tải zip rồi giải nén ra cũng được

```sh
  pkg install git
```
Với các thiết bị chạy linux, chỉ cần sửa 'pkg' thành 'apt' hoặc trình quản lí gói nào đấy tùy theo linux distro của bạn

Sau khi cài xong thư viện thì clone source

```sh
  git clone https://github.com/congthcstp/AutoDangKiTin_TLU
```
Rồi truy cập vào thư mục chứa source code:

```sh
  cd AutoDangKiTin_TLU
```
Cuối cùng là chạy với lệnh:
```sh
  python3 script.py
```
Lưu ý: có thể sẽ cần sửa 'python3' thành 'python' nếu không có lệnh 'python'

Tính năng auto đăng kí tín hiện đang gặp 1 chút trục trặc nên chưa thể hoạt động, có thể tôi sẽ update sau :3
