# tiktok-dl

[English](README.md) · **Tiếng Việt**

> [!WARNING]
> **Dự án chỉ phục vụ mục đích học tập.** Không dùng để đăng lại hay bán nội dung của người khác.

Tải toàn bộ video của một profile TikTok, hoặc từng reel Instagram, Facebook, kèm đầy đủ thông tin.

- Tải nhanh bằng **aria2c**
- **ffmpeg** gắn tiêu đề, tác giả, ngày đăng, mô tả, lượt xem, ảnh bìa, phụ đề vào từng file MP4
- Bài đăng dạng ảnh được ghép thành video slideshow
- Lưu toàn bộ thông tin vào `videos.csv` và `metadata/*.json`
- Tắt giữa chừng thì chạy lại để tải tiếp

## Cài đặt

1. Cài [Python 3.10 trở lên](https://www.python.org/downloads/)
2. Chạy:
   ```bash
   pip install -r requirements.txt
   ```
   Trên Linux/macOS thì dùng `python3 -m pip install -r requirements.txt`.
   Nếu pip báo môi trường "externally managed", hãy tạo môi trường ảo trước:
   `python3 -m venv .venv && . .venv/bin/activate`
3. Cài aria2c và ffmpeg (cả hai đều không bắt buộc, xem bên dưới):
   - **Windows:** đặt `aria2c.exe` và `ffmpeg.exe` cạnh file `tiktok-dl.py`
     - aria2c: https://github.com/aria2/aria2/releases
     - ffmpeg: https://www.gyan.dev/ffmpeg/builds/
   - **Debian/Ubuntu:** `sudo apt install aria2 ffmpeg`
   - **macOS** (dùng [Homebrew](https://brew.sh)): `brew install aria2 ffmpeg`

Tool tìm từng chương trình cạnh file `tiktok-dl.py` trước, sau đó mới tìm trong `PATH`.

- **ffmpeg:** nếu không tìm thấy, tool dùng ffmpeg đi kèm gói `imageio-ffmpeg`
  (đã cài ở bước 2), nên không cài ffmpeg riêng cũng được.
- **aria2c:** nếu không có, tool tải bằng `requests` của Python (chậm hơn).

### Hoặc cài thành lệnh `tiktok-dl`

Thay cho bước 2 và 3 ở trên, bạn có thể cài `tiktok-dl` thành một lệnh bằng
[pipx](https://pipx.pypa.io) (hoặc `uv tool install`), không cần clone repo:

```bash
pipx install git+https://github.com/Aluminat/tiktok-dl
tiktok-dl @username
```

Sau đó dùng `tiktok-dl` ở chỗ nào các lệnh bên dưới ghi `python tiktok-dl.py`.
Cập nhật bằng `pipx upgrade tiktok-dl`, gỡ bằng `pipx uninstall tiktok-dl`.
Bản cài theo cách này tìm aria2c và ffmpeg qua `PATH` (trên Windows, hãy đặt các file `.exe`
vào một thư mục có trong `PATH`), và vẫn dùng ffmpeg của `imageio-ffmpeg` nếu không tìm thấy.

Trên Linux/macOS, dùng `python3` thay cho `python` trong các lệnh bên dưới.

## Cách dùng

```bash
python tiktok-dl.py @username
```

| Lệnh | Tác dụng |
|---|---|
| `python tiktok-dl.py @username` | Tải hết profile |
| `python tiktok-dl.py @username -n 20` | Chỉ tải 20 bài mới nhất |
| `python tiktok-dl.py @username -o D:\TikTok` | Lưu vào thư mục khác |
| `python tiktok-dl.py @username --metadata-only` | Chỉ lấy thông tin, không tải video |
| `python tiktok-dl.py <link video>` | Tải một video |
| `python tiktok-dl.py https://vt.tiktok.com/XXXXXXXXX/` | Link rút gọn copy từ app TikTok cũng được |
| `python tiktok-dl.py @username --lang vi` | Hiện thông báo bằng tiếng Việt |
| `python tiktok-dl.py @username --retry-all` | Thử lại cả bài đã xoá và bài quảng cáo / TikTok Shop mà các lần chạy lại bỏ qua |
| `python tiktok-dl.py https://www.instagram.com/reel/XXXXXXXXXXX/` | Tải một reel Instagram (cần đăng nhập, xem [Instagram](#instagram)) |
| `python tiktok-dl.py https://www.facebook.com/reel/1234567890123456` | Tải một reel Facebook (xem [Facebook](#facebook)) |

File được lưu trong `downloads/<username>/`.

### Trang web và thư mục

Tool tự nhận ra link thuộc trang nào:

| Link | Trang |
|---|---|
| `tiktok.com/@username`, `.../video/...`, `.../photo/...`, `vt.tiktok.com/...`, `vm.tiktok.com/...` | TikTok |
| `instagram.com/<username>`, `.../reel/...`, `.../reels/...`, `.../p/...`, `.../tv/...` (cả `www.` và `m.`) | Instagram |
| `facebook.com/reel/...`, `.../watch/?v=...`, `.../<page>/videos/...`, `.../<page>/reels/...`, `.../share/r/...`, `.../share/v/...` (cả `www.`, `m.` và `web.`), `fb.watch/...` | Facebook |

**Instagram:** tải được link từng reel và bài video (xem [Instagram](#instagram)). Chưa hỗ trợ cả
profile Instagram, bài ảnh và bài nhiều ảnh/video. **Facebook:** tải được link từng reel
(`facebook.com/reel/...`, `.../watch/?v=...` và `fb.watch/...`, xem [Facebook](#facebook)). Các link
Facebook khác (trang, `.../videos/...`, `.../share/...`) báo lỗi kèm các dạng link được hỗ trợ, rồi
tool chuyển sang mục tiếp theo.

Không có `--site` thì `username` hoặc `@username` không kèm link được hiểu là TikTok, còn link của
trang nào cũng được nhận. Khi có `--site instagram`, `--site facebook` hoặc `--site tiktok`, username
thuộc trang đó và link phải thuộc đúng trang đó: link của trang khác sẽ báo lỗi, rồi tool chuyển
sang mục tiếp theo.

Mỗi tài khoản có một thư mục riêng:

| Trang | Thư mục |
|---|---|
| TikTok | `downloads/<username>/` |
| Instagram | `downloads/instagram/<username>/` |
| Facebook | `downloads/facebook/<id trang>/` (số id của trang, hoặc id dạng `pfbid...`) |

Link một bài đăng được lưu vào thư mục của tác giả bài đó, nên mỗi tài khoản chỉ có một
`videos.csv`, một `failed.txt` và một file tiến độ. TikTok không có thư mục `tiktok/` vì trước đây
tool chỉ tải TikTok: thư mục do các bản cũ tạo vẫn giữ nguyên chỗ, và khi chạy lại tool vẫn bỏ qua
các bài đã tải trong đó.

Thông báo mặc định bằng **tiếng Anh**, máy cài tiếng Việt thì tự chuyển sang tiếng Việt.
Dùng `--lang vi` hoặc `--lang en` để tự chọn.

Chữ có màu (xanh lá = xong, đỏ = lỗi, vàng = lý do / cảnh báo) kèm thanh tiến độ chạy trực tiếp.
Dùng `--no-color` (hoặc đặt biến `NO_COLOR=1`) để in chữ trơn.

Chạy `python tiktok-dl.py -h` để xem tất cả tuỳ chọn.

## Tốc độ và giới hạn request

Mặc định được để thấp để trang web ít chặn hay đánh dấu tài khoản của bạn
(càng nên cẩn thận khi đã đăng nhập). Mỗi trang có mặc định riêng; các tuỳ chọn sẽ ghi đè:

| Tuỳ chọn | TikTok | Instagram | Facebook | Tác dụng |
|---|---|---|---|---|
| `-j`, `--jobs` | 16 | 16 | 16 | số file aria2c tải cùng lúc |
| `-x`, `--connections` | 4 | 4 | 4 | số kết nối aria2c cho mỗi file (tối đa 16) |
| `-w`, `--workers` | 8 | 1 | 1 | số luồng đọc trang bài đăng |
| `--wait` | 0.3 | 3 | 2 | số giây giãn cách giữa các request trang |

Mặc định của Instagram và Facebook là ước lượng thận trọng, chưa đo đạc thực tế.

Bạn có thể tăng lên, ví dụ `-x 8 --wait 0.1`, nhưng tải càng nhanh thì càng dễ bị chặn.

Nếu TikTok trả HTTP 429 (quá nhiều request) hoặc 403 khi đọc trang, tool hiện cảnh báo một lần,
rồi tạm dừng và thử lại, mỗi lần chờ lâu hơn (5, 10, 20 giây), hoặc chờ đúng thời gian TikTok
yêu cầu trong header `Retry-After` (tối đa 2 phút). Nếu bị liên tiếp 8 lần, tool sẽ dừng hẳn thay vì
thử lại từng bài (có thể TikTok đang tạm chặn IP của bạn); các bài đã xong vẫn được giữ và
`failed.txt` ghi lý do dừng. Hãy đợi một lúc rồi chạy lại đúng lệnh cũ
để tải tiếp; tăng `--wait` hoặc giảm `--workers` cũng giúp ích.

## Log bài không tải được

Bài nào không tải được, tool sẽ báo cho bạn:

- Trên màn hình, mỗi bài lỗi hiện kèm **link bài đăng** và **lý do**.
- Toàn bộ bài lỗi được lưu vào file **`failed.txt`** trong thư mục tải, để bạn tự mở từng link ra kiểm tra.

```
https://www.tiktok.com/@username/video/7705012484368780552
    video quảng cáo / TikTok Shop: TikTok không cung cấp link tải qua web (kể cả khi đã đăng nhập)
```

`failed.txt` được cập nhật sau mỗi lần chạy và liệt kê mọi bài của kênh vẫn đang lỗi, kể cả bài lỗi từ những lần chạy trước (tải một video lẻ cũng không làm mất danh sách này). File tự xoá khi không còn bài nào lỗi.

Bài không bao giờ tải được (bài đã bị xoá, bài quảng cáo / TikTok Shop mà TikTok không cho link tải) chỉ báo lỗi một lần, sau đó được ghi `(bỏ qua)` trong `failed.txt`. Các lần chạy lại sẽ bỏ qua chúng mà không cần mở trang bài; còn các lỗi khác (lỗi mạng, bị giới hạn tốc độ, lỗi ffmpeg) vẫn được thử lại mỗi lần chạy. Thêm `--retry-all` để thử lại cả các bài bị bỏ qua, ví dụ khi TikTok thay đổi cách trả dữ liệu.

## Lấy session ID (không bắt buộc)

Chỉ cần khi tải **tài khoản riêng tư bạn đang theo dõi** hoặc **bài giới hạn độ tuổi**.

1. Mở https://www.tiktok.com bằng **Chrome** hoặc **Edge** và đăng nhập.
2. Bấm **F12** để mở DevTools.
3. Vào tab **Application** → **Storage** → **Cookies** → `https://www.tiktok.com`.
   (Firefox: tab **Storage** → **Cookies**.)
4. Gõ `sessionid` vào ô lọc (Filter).
5. Bấm đúp vào cột **Value** của dòng có tên đúng là `sessionid`, rồi copy (là một chuỗi dài gồm chữ và số).
6. Lưu nó vào biến môi trường `TIKTOK_SESSIONID` (nên dùng cách này). Các lệnh dưới đây sẽ hỏi
   giá trị để bạn dán vào, nên nó không bị lưu vào lịch sử lệnh.
   Biến này có hiệu lực cho tới khi bạn đóng cửa sổ terminal.

   PowerShell (Windows):
   ```powershell
   $env:TIKTOK_SESSIONID = Read-Host "sessionid"
   ```
   bash / zsh (Linux, macOS, Git Bash):
   ```bash
   read -rs -p "sessionid: " TIKTOK_SESSIONID && export TIKTOK_SESSIONID
   ```
   (zsh: `read -rs "TIKTOK_SESSIONID?sessionid: " && export TIKTOK_SESSIONID`)
7. Kiểm tra session có dùng được không:
   ```bash
   python tiktok-dl.py --check-session --lang vi
   ```
   - `✓ Session hợp lệ: đang đăng nhập là @tai_khoan_cua_ban` → dùng được.
   - `✗ Session ID không hợp lệ hoặc đã hết hạn` → copy lại cái mới.
8. Tải kèm session:
   ```bash
   python tiktok-dl.py @username
   ```
   Tool sẽ tự kiểm tra session trước, nếu không hợp lệ thì dừng lại.

Bạn vẫn có thể truyền thẳng bằng `--session-id DAN_SESSIONID_VAO_DAY`, nhưng như vậy nó sẽ bị lưu
trong lịch sử lệnh. Nếu đặt cả hai thì `--session-id` được ưu tiên.

> [!CAUTION]
> `sessionid` có tác dụng như mật khẩu. **Tuyệt đối không chia sẻ** hay đăng lên mạng.
> Nếu bạn đăng xuất TikTok thì session ID sẽ hết hiệu lực, cần copy lại cái mới.
> Nên dùng **tài khoản phụ** cho an toàn.

## Instagram

Tải được link từng **reel** và **bài video**: `instagram.com/reel/...`, `.../reels/...`,
`.../p/...` (khi bài chỉ có một video) và `.../tv/...`, kể cả khi có username phía trước
(`instagram.com/<username>/reel/...`). Chưa hỗ trợ cả profile, bài ảnh và bài nhiều ảnh/video.
Mỗi reel được lưu vào `downloads/instagram/<username>/`, cũng gắn metadata MP4, ghi `videos.csv`,
`failed.txt` và tải tiếp được như TikTok.

> [!WARNING]
> Instagram chỉ tải được khi **đã đăng nhập**, bằng cookie `sessionid` của tài khoản, và **Meta có
> thể khoá tài khoản tải tự động**. Hãy dùng **tài khoản phụ**, đừng dùng tài khoản chính.

1. Mở https://www.instagram.com bằng **Chrome** hoặc **Edge** và đăng nhập (tài khoản phụ).
2. Nhấn **F12**, vào **Application** → **Storage** → **Cookies** → `https://www.instagram.com`
   (Firefox: tab **Storage** → **Cookies**).
3. Gõ `sessionid` vào ô lọc và copy **Value** của dòng tên đúng là `sessionid`.
4. Đặt nó vào biến môi trường `INSTAGRAM_SESSIONID`. Các lệnh dưới đây hỏi giá trị rồi bạn dán vào,
   nên nó không bị lưu trong lịch sử lệnh:

   PowerShell (Windows):
   ```powershell
   $env:INSTAGRAM_SESSIONID = Read-Host "sessionid"
   ```
   bash / zsh (Linux, macOS, Git Bash):
   ```bash
   read -rs -p "sessionid: " INSTAGRAM_SESSIONID && export INSTAGRAM_SESSIONID
   ```
   (zsh: `read -rs "INSTAGRAM_SESSIONID?sessionid: " && export INSTAGRAM_SESSIONID`)
5. Tải:
   ```bash
   python tiktok-dl.py https://www.instagram.com/reel/XXXXXXXXXXX/
   ```

Dùng `--ig-session-id DAN_VAO_DAY` cũng được, nhưng sẽ bị lưu trong lịch sử lệnh; nó được ưu tiên hơn
biến môi trường. Cookie chỉ được gửi tới instagram.com: không bao giờ được in ra, ghi vào file hay
đưa cho aria2c. Không có cookie thì tool báo rõ và không gửi request nào.

Tool hỏi Instagram từng bài giống cách trang web của Instagram làm, và chậm (3 giây giữa các request,
mỗi lần một bài). Khi Instagram trả về:

- **quá nhiều request (HTTP 429):** tool chậm lại rồi thử lại, như với TikTok.
- **bước kiểm tra (`checkpoint_required` / `challenge_required`):** tool dừng ngay. Hãy mở Instagram
  trên trình duyệt bằng đúng tài khoản đó và tự hoàn tất bước kiểm tra, rồi chạy lại đúng lệnh cũ.
  Tool không bao giờ tự giải hay bỏ qua bước này.
- **`login_required`:** tool dừng; cookie đã hết hạn hoặc sai, hãy copy cookie mới.
- **`feedback_required`:** tool dừng; Instagram đang hạn chế tài khoản. Hãy đợi vài giờ hoặc một ngày.

Bài đã xoá và bài tài khoản của bạn không xem được sẽ được ghi `(bỏ qua)` trong `failed.txt` (khi link
có username, để tool biết thư mục) và các lần chạy lại sẽ bỏ qua chúng; thêm `--retry-all` để thử lại.
Link `/reel/...` không có username sẽ được đọc một lần mỗi lần chạy để tìm thư mục của tác giả.

## Facebook

Tải được link từng **reel**: `facebook.com/reel/<id>`, `facebook.com/watch/?v=<id>` (cả `m.` và
`web.`) và link rút gọn `fb.watch/...` (tool mở link rút gọn để tìm reel trước). Chưa hỗ trợ cả
trang, story và highlight. Mỗi reel được lưu vào `downloads/facebook/<id trang>/`, cũng gắn metadata
MP4, ghi `videos.csv`, `failed.txt` và tải tiếp được như TikTok. Với reel, Facebook không trả tên
trang rõ ràng, nên thư mục dùng id của trang; tên trang nằm ở cột `nickname` của `videos.csv` và
trong metadata MP4.

```bash
python tiktok-dl.py https://www.facebook.com/reel/1234567890123456
```

Cách hoạt động: [yt-dlp](https://github.com/yt-dlp/yt-dlp) **chỉ được dùng để tìm** link video,
link âm thanh và thông tin của reel (bộ trích xuất Facebook của nó, dùng như thư viện). aria2c tải
video và âm thanh (không có aria2c thì dùng Python `requests`), rồi ffmpeg ghép chúng thành một file
MP4 kèm metadata. Tool không bao giờ dùng bộ tải hay các bước ffmpeg của yt-dlp. Không có ffmpeg thì
video và âm thanh được lưu thành 2 file (`.mp4` và `.m4a`).

Tool đọc từng reel chậm (2 giây giữa các request, mỗi lần một reel). Khi Facebook trả về:

- **quá nhiều request (HTTP 429):** tool chậm lại rồi thử lại, như với TikTok.
- **yêu cầu đăng nhập:** link đó báo lỗi và hướng dẫn dùng `--cookies` (xem bên dưới), rồi tool
  chuyển sang mục tiếp theo.
- **reel không tồn tại hoặc đã bị xoá:** link đó báo lỗi kèm lý do. Vì link reel không cho biết trang,
  tool không có thư mục để ghi `(bỏ qua)`, nên lần chạy sau reel sẽ được đọc lại.

Tool không lách bất cứ thứ gì: không giả trình duyệt hay giả bot thu thập của Facebook, và không bao
giờ tìm cách vượt qua bước kiểm tra.

### `--cookies`: reel cần đăng nhập

Phần lớn reel công khai tải được mà không cần đăng nhập. Với reel bị yêu cầu đăng nhập, hãy đưa cho
tool cookie Facebook của bạn dưới dạng **file cookie Netscape** (`cookies.txt`):

> [!WARNING]
> **Meta có thể khoá tài khoản tải tự động.** Hãy dùng **tài khoản phụ**, đừng dùng tài khoản chính.
> File cookie có giá trị như mật khẩu: đừng chia sẻ, và xoá nó khi dùng xong.

1. Đăng nhập https://www.facebook.com trên **Chrome**, **Edge** hoặc **Firefox** (tài khoản phụ).
2. Cài một tiện ích trình duyệt xuất cookie theo định dạng **Netscape** (`cookies.txt`), rồi xuất
   cookie của `facebook.com` ra file, ví dụ `fb-cookies.txt`.
3. Tải:
   ```bash
   python tiktok-dl.py https://www.facebook.com/reel/1234567890123456 --cookies fb-cookies.txt
   ```

Đăng nhập Facebook dùng nhiều cookie (`c_user`, `xs`, ...), nên tool nhận cả file chứ không phải một
giá trị. File chỉ được đọc trong bộ nhớ: nội dung không bao giờ được in ra, chép ra đĩa hay đưa vào
dòng lệnh, và tool chỉ dùng cookie `facebook.com` trong file, chỉ cho facebook.com (không bao giờ
cho file video, không bao giờ đưa cho aria2c). Nếu vẫn bị yêu cầu đăng nhập, cookie đã đăng xuất hoặc
hết hạn: hãy xuất lại.

`--cookies` cũng dùng được cho **Instagram**: khi không có `--ig-session-id` / `INSTAGRAM_SESSIONID`,
tool lấy cookie `sessionid` của `instagram.com` trong file. Vì vậy một file xuất khi đang đăng nhập cả
hai trang dùng được cho cả hai.

## Gặp lỗi?

- **Không lấy được danh sách bài:** chạy `pip install -U yt-dlp`
- **Vài bài bị lỗi:** xem file `failed.txt` trong thư mục tải (có link và lý do từng bài), rồi chạy lại đúng lệnh cũ
- **Video quảng cáo / TikTok Shop bị lỗi:** TikTok không cung cấp link tải cho loại video này qua web, kể cả khi đã đăng nhập. Chúng được liệt kê trong `failed.txt` để bạn tự mở xem, và các lần chạy lại sẽ bỏ qua chúng (thêm `--retry-all` để thử lại)
- **Tài khoản riêng tư:** đặt biến `TIKTOK_SESSIONID` (hoặc thêm `--session-id`), xem [Lấy session ID](#lấy-session-id-không-bắt-buộc)
- **Instagram dừng với "qua bước kiểm tra", "chưa đăng nhập" hoặc "đang hạn chế tài khoản":** xem [Instagram](#instagram)
- **"Facebook yêu cầu đăng nhập để xem reel này":** thêm `--cookies <file>`, xem [Facebook](#facebook). **"Facebook không trả dữ liệu reel":** chạy `pip install -U yt-dlp`
- **"Lớp chống bot của TikTok đã chặn request (trang xác minh)":** TikTok trả về trang kiểm tra "Please wait..." thay vì trang thật. Tool không thử lại trường hợp này: nếu trang profile bị chặn, profile đó sẽ bị bỏ qua; nếu 3 trang bài đăng liên tiếp bị chặn, tool sẽ dừng (các bài đã xong vẫn được giữ và `failed.txt` ghi rõ lý do). Hãy thử lại sau hoặc dùng mạng khác (tắt VPN, đổi kết nối). `--session-id` không giúp được trường hợp này

## Đóng góp

Mọi báo lỗi và pull request đều được hoan nghênh. Xem [CONTRIBUTING.md](CONTRIBUTING.md) (tiếng Anh).

## Miễn trừ trách nhiệm

**Dự án chỉ phục vụ mục đích học tập.** Bạn tự chịu trách nhiệm khi sử dụng.
Hãy tôn trọng bản quyền của người đăng và Điều khoản dịch vụ của TikTok.

Dự án không liên quan tới TikTok hay ByteDance. Tác giả không chịu trách nhiệm cho bất kỳ hành vi sử dụng sai mục đích nào.

## License

MIT © doumamay
