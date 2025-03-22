# AutoDangKiTin_TLU

Không gì khác ngoài 1 project nho nhỏ của 1 thằng sv năm 2 khoa CNTT

Tính năng chính:
- Auto đăng kí tín chỉ (Đang trong giai đoạn thử nghiệm)
- Tự động gửi lịch học lên google Lịch

Lưu ý: Nếu trong quá trình gửi lịch lên google Lịch bị trục trặc, bạn nên xóa toàn bộ lịch trước đó bằng tay để tránh sự trùng lặp

## Tại sao lại là script của tôi mà không phải 1 cái app nào đấy trên Google Play ?
1. Nó miễn phí: Bạn không phải trả 1 đồng nào hay xem 1 cái quảng cáo nào để kiếm tiền cho tôi cả
2. Nó open source: Bạn có thể thêm, sửa, xóa source của tôi để phù hợp với mục đích của bạn, và bạn cũng có thể yên tâm là tôi chả húp tí thông tin nào của bạn đâu
3. Nó có thể gửi lịch của bạn trực tiếp lên google Lịch: Tin tôi đi, google Lịch uy tín hơn nhiều so với 1 cái app nào đấy mà sẽ luôn chậm thông báo và đôi khi còn thông báo những cái đ** ai hỏi. Nó còn trực quan hơn nữa
4. Nó có thể tự động đăng kí tín chỉ: Mặc dù tính năng này chỉ đang trong giai đoạn phát triển, nhưng việc nó hoàn thiện chỉ là sớm hay muộn mà thôi. Đến lúc đó bạn sẽ không bao giờ phải canh từng giây để đăng kí môn nữa, script sẽ làm hộ bạn từ A-Z

## Hướng dẫn
Các bước hướng dẫn dưới đây là dành cho thiết bị Android (termux), các thiết bị chạy Windows/Linux/MacOS cũng làm tương tự

Cài đặt termux tại đây: https://github.com/termux/termux-app/releases

Cài đặt python cho Windows tại đây: https://apps.microsoft.com/detail/9ncvdn91xzqp?ocid=webpdpshare

Với các thiết bị chạy Linux, chỉ cần sửa 'pkg' thành 'apt' hoặc trình quản lí gói nào đấy tùy theo linux distro của bạn

Script yêu cầu python (hiển nhiên rồi, cái này được viết bằng python mà :v) và 1 số thư viện cài qua lệnh pip:
```sh
pkg install python git
```
```sh
git clone https://github.com/congthcstp/AutoDangKiTin_TLU
```
```sh
cd AutoDangKiTin_TLU
```
```sh
pip install -r requirements.txt
```
```sh
python3 script.py
```
Lưu ý: có thể sẽ cần sửa 'python3' thành 'python' nếu không có lệnh 'python3'
