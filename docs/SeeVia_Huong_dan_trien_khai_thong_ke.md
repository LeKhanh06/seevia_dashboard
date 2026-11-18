# Hướng dẫn triển khai thống kê sử dụng SeeVia

Nguồn nghiệp vụ là tài liệu `SeeVia_Dinh_huong_thong_ke_su_dung.docx` ngày 14/11/2026.

Phạm vi giai đoạn này gồm thu thập sự kiện, tổng hợp số liệu và hai API thống kê. Chủ kính xem mức sử dụng của mình. Admin dùng số liệu toàn hệ thống để báo cáo, đánh giá pilot, theo dõi chất lượng và ước tính tải.

## Các điểm cần xác nhận khi tích hợp

- Chọn nhánh gốc hoặc hợp nhất thay đổi `hearfix` trước khi gắn điểm đo. Đối chiếu các vị trí gọi chức năng, phát âm thanh và nhận callback trên nhánh được chọn.
- Kiểm tra schema và luồng đăng ký kính để xác định `glasses_devices.user_id` tham chiếu `users.id` hay `public_user_id`. Dùng ánh xạ đã xác minh để gắn đúng chủ kính cho sự kiện và lọc `/me/stats`.
- Xác định chỗ truyền trạng thái qua `Reply`, tín hiệu phát xong ở luồng A/B và cách `run_action` trả mã lỗi ngắn. Đối chiếu các callback xác nhận thành công, thất bại hoặc hủy ở luồng điện thoại.
- Cấu hình scope `usage:write` cho luồng VH gửi dữ liệu sang AB và token admin riêng trên AB. Giữ token ngoài log.

## Đơn vị đo và mốc chốt kết quả

| Đơn vị | Quy tắc ghi nhận |
| --- | --- |
| **Lượt** | Một lần gọi chức năng. Ghi mốc bắt đầu một lần cho mỗi lượt. Một lần thử lại do người dùng thực hiện là một lượt mới. |
| **Tác vụ** | Chuỗi lượt của cùng chủ kính, cùng chức năng. Ghép với lượt liền trước khi hai lượt cách nhau tối đa 60 giây và lượt trước vẫn chưa kết thúc luồng, chẳng hạn đang chờ người dùng chụp lại. Một lượt gọi sau khi kết quả cuối đã được xác nhận bắt đầu tác vụ mới. |
| **Phiên** | Khoảng sử dụng liên tục của live mode, kết nối kính hoặc cuộc gọi. Ghi mốc đầu và cuối theo vòng đời thực tế của phiên. |

Ở luồng A/B, `Reply` mang trạng thái kết quả dự kiến và mã lỗi nếu có. Tầng phát ghi mốc bắt đầu phát âm thanh thực tế để đo độ trễ tiếng đầu. Nếu luồng cần phát lời đáp, chốt khi tầng phát xác nhận phát xong hoặc báo lỗi phát.

Ở luồng điện thoại, lưu lượt đang chờ theo `request_id`. `run_action` trả mã lỗi ngắn khi có lỗi. Dùng callback của hành động để xác nhận kết quả thực tế theo cùng `request_id`. Nếu chức năng cần cả hành động điện thoại và lời đáp, chỉ chốt thành công khi hành động đã được xác nhận và lời đáp đã phát xong. Ghi nhận việc hỏi lại và hủy trong cửa sổ cho phép tại điểm luồng xử lý các tình huống đó.

**Quy ước triển khai đề xuất.** Đo khoảng 60 giây từ mốc gọi lượt trước đến mốc gọi lượt sau. Khi có nhiều yêu cầu song song, dùng mã ngữ cảnh hoặc tín hiệu thử lại của luồng để chọn lượt cần ghép. Mở tác vụ riêng nếu chưa xác định được quan hệ giữa hai lượt.

Chốt tác vụ bằng kết quả cuối của luồng. Ghi tín hiệu ảnh bị từ chối tại bước kiểm tra ảnh. Nếu luồng yêu cầu chụp lại, giữ trạng thái chờ để lượt tiếp theo có thể được ghép vào tác vụ. Chỉ tính bỏ cuộc khi đã hết thời hạn đợi người dùng theo quy tắc của chức năng. Chốt thời hạn đợi này riêng với cửa sổ ghép 60 giây. Khi luồng điện thoại xác nhận đã quá hạn chờ callback, ghi kết quả lỗi kỹ thuật với mã lỗi tương ứng.

## Chỉ số theo chức năng

| Chức năng | Số liệu cần trả | Mốc hoặc tín hiệu cần ghi |
| --- | --- | --- |
| Đọc chữ `ocr` | Số lượt, tỷ lệ hoàn thành, số ảnh bị từ chối, số lần thử lại và thời gian xử lý | Ghi từng lần ảnh bị từ chối, thời lượng xử lý OCR và kết quả khi lời đáp kết thúc. |
| Nhận tiền `money` | Số lượt, tỷ lệ `uncertain`, số ảnh bị từ chối và tỷ lệ hoàn thành | Ghi trạng thái nhận diện và tín hiệu từ chối ảnh. Đánh giá nhận diện đúng bằng dữ liệu có nhãn đối chiếu. |
| Tìm đồ `find`, mô tả cảnh `space`, hỏi ảnh `ask`, hỏi vật cản `hazard` | Số lượt, phân bố trạng thái kết quả, số ảnh bị từ chối và thời lượng | Ghi kết quả cuối, tín hiệu từ chối ảnh và mốc đầu cuối của lượt. Tổng hợp riêng từng chức năng. |
| Trò chuyện `chat` | Số lượt, tỷ lệ cần tra web, độ trễ tiếng đầu và thời lượng | Đánh dấu tại điểm luồng quyết định cần tra web. Ghi mốc phát tiếng đầu và mốc kết thúc. |
| Cảnh báo vật cản live `alert_mode` | Số phiên, tổng phút bật, số cảnh báo mỗi giờ bật và phân bố theo mức cảnh báo | Ghi lúc bật/tắt và mỗi cảnh báo đã phát cùng mã mức. Dùng phản hồi người dùng hoặc bộ dữ liệu đánh giá riêng để đo báo oan. |
| Đọc chữ live `ocr_live` | Số phiên, thời gian căn khung và thời lượng phiên | Ghi mốc đầu cuối phiên, lúc bắt đầu căn và lúc khung đạt yêu cầu. |
| Gọi người thân, nhắn tin, đọc tin | Số lượt, tỷ lệ hủy trong cửa sổ cho phép, tỷ lệ hỏi lại, số lỗi theo mã và thời gian đến xác nhận | Ghi tín hiệu hỏi lại, hủy hợp lệ và mốc callback xác nhận kết quả. Đếm riêng từng chức năng. |
| Khẩn cấp `emergency_call` | Số lần kích hoạt, tỷ lệ thành công/thất bại và thời gian đến khi có người nghe hoặc xác nhận | Ghi mốc kích hoạt, mốc có người nghe hoặc có xác nhận theo luồng SOS và mã lỗi cuối nếu có. |
| Nhạc, dẫn đường, đặt xe | Số lượt, thời lượng, tỷ lệ hủy và số lỗi theo mã | Gắn đo lường khi chức năng ngoài MVP tương ứng được triển khai. Ghi trạng thái hủy và lỗi tại mốc kết thúc. |
| Gọi qua Internet đi hoặc đến | Số cuộc, tỷ lệ nghe máy, thời lượng và số lỗi kết nối | Ghép trạng thái theo mã cuộc gọi kỹ thuật, ghi chiều gọi, mốc bắt đầu, nghe máy và kết thúc. |
| Kết nối kính `connection` | Số phiên, tổng thời gian kết nối và số ngày có hoạt động | Ghi lúc kết nối thành công và lúc ngắt kết nối. Chỉ dùng nhãn “thời gian đeo” khi có tín hiệu trạng thái đeo đáng tin cậy. |

## Công thức tổng hợp

Tỷ lệ hoàn thành dùng **tác vụ** làm đơn vị tính. Các mẫu số và quy ước thời gian bên dưới là đề xuất triển khai cho những chi tiết tài liệu gốc chưa chốt.

| Chỉ số | Cách tính |
| --- | --- |
| Lượt và độ phủ | Đếm mã lượt duy nhất theo chức năng. Đếm số chủ kính khác nhau có lượt, phiên hoặc cuộc gọi tương ứng. |
| Tỷ lệ hoàn thành và hoàn thành lần đầu | Chia số tác vụ thành công cho số tác vụ đã chốt. Với chỉ số lần đầu, tử số là số tác vụ thành công chỉ sau một lượt. |
| Thử lại và bỏ cuộc | Số lần thử lại của một tác vụ bằng số lượt trừ một. Lấy trung bình trên tác vụ đã chốt. Tỷ lệ bỏ cuộc bằng số tác vụ bỏ cuộc chia số tác vụ đã chốt. Giữ hủy, lỗi và bỏ cuộc thành các kết quả riêng. |
| Kết quả và lỗi | Đếm theo trạng thái đã xác nhận và mã lỗi ngắn. Báo riêng tác vụ đang chờ để đọc đúng số liệu của kỳ gần nhất. |
| Độ trễ và thời gian đến kết quả cuối | Tính p50/p90 của khoảng từ lúc gọi chức năng đến lúc bắt đầu phát tiếng đầu. Tính thời gian đến kết quả cuối từ lúc gọi đến mốc chốt của lượt. Mỗi phép tính chỉ dùng mẫu có đủ hai mốc của chỉ số đó. |
| Tỷ lệ theo chức năng | Chia số lượt có tín hiệu cho số lượt đủ điều kiện. `uncertain` dùng lượt có kết quả nhận diện tiền. Cần tra web dùng lượt chat đã đến bước quyết định tra web. Hủy trong cửa sổ dùng lượt đủ điều kiện hủy trong cửa sổ đó. Hỏi lại dùng lượt đã đến bước xác định lệnh hoặc người nhận. Với nhạc, dẫn đường, đặt xe, tính tỷ lệ hủy trên các lượt đã chốt. Tỷ lệ nghe máy dùng các cuộc đã biết kết quả. Tỷ lệ thành công và thất bại SOS dùng các lượt SOS đã chốt. |
| Thời lượng phiên và cảnh báo mỗi giờ | Cộng phần thời lượng đã xác nhận nằm trong kỳ báo cáo. Chia số cảnh báo phát trong các khoảng đó cho tổng số giờ bật `alert_mode` cùng kỳ. Thời lượng cuộc gọi đã nghe tính từ lúc nghe máy đến lúc kết thúc. |
| Phân bố theo thời điểm | Dùng mốc phát sinh của lượt, phiên hoặc cuộc gọi. Nhóm theo ngày, tuần, tháng để xem xu hướng, và nhóm theo giờ trong ngày hoặc thứ trong tuần để xem giờ cao điểm. |
| DAU, WAU, MAU và người dùng mới | Đếm chủ kính có hoạt động trong ngày báo cáo, 7 ngày và 30 ngày kết thúc vào ngày báo cáo. Đếm người dùng mới theo mốc hoạt động đầu tiên đã ghi nhận trong toàn bộ lịch sử đo. |
| Kính hoạt động và số ngày sử dụng | Đếm kính khác nhau có hoạt động trong kỳ. Đếm số ngày lịch riêng biệt có hoạt động theo chủ kính hoặc kính. Dùng lượt thực tế hoặc khoảng phiên có sử dụng đã xác nhận làm tín hiệu hoạt động. |

Đo thời gian xử lý OCR từ lúc bắt đầu xử lý ảnh đến lúc có kết quả OCR. Đo thời gian căn khung từ lúc bắt đầu căn đến lúc khung đạt yêu cầu. Với chỉ số thời gian đến xác nhận của điện thoại và SOS, tính từ lúc gọi chức năng đến mốc xác nhận tương ứng trong bảng chỉ số.

Lưu thời điểm theo UTC và dùng cùng múi giờ báo cáo khi nhóm thời gian. Kỳ báo cáo bao gồm mốc đầu và loại trừ mốc cuối. Đếm lượt và phiên theo mốc bắt đầu của từng bản ghi. Tính các chỉ số theo tác vụ trong kỳ chứa mốc bắt đầu tác vụ, kể cả khi kết quả được xác nhận vào ngày sau. Số liệu kỳ gần nhất có thể cập nhật khi nhận kết quả muộn.

Với phiên qua ngày, chia thời lượng theo phần giao với từng ngày. Khi thiếu tín hiệu kết thúc phiên, chỉ cộng thời lượng có thể xác nhận. Nếu cả hai đầu cuộc gọi Internet cùng gửi sự kiện, tổng hợp toàn hệ thống đếm mỗi mã cuộc gọi một lần. Trả `0` cho số đếm rỗng và `null` cho tỷ lệ hoặc phân vị thiếu mẫu tính.

## Thu thập và lưu dữ liệu

VH ghi sự kiện tại các mốc nghiệp vụ và gửi theo lô sang AB. AB xác thực bên gửi, kiểm tra dữ liệu, loại trùng và lưu vào PostgreSQL. Hai API đọc từ cùng nguồn dữ liệu đã chuẩn hóa.

| Dữ liệu vận hành | Dùng để tính |
| --- | --- |
| Mã sự kiện, mã lượt, mã chức năng và thời điểm phát sinh | Đếm lượt, đối soát gửi lại, lọc theo thời gian và nhóm chức năng. |
| Mã liên kết chủ kính và kính đã được bảo vệ | Tính số người dùng/kính khác nhau và lọc theo chính chủ. |
| Mã ghép cần thiết như `request_id`, mã phiên hoặc mã cuộc gọi | Ghép callback, mốc đầu cuối và trạng thái của cùng một lượt, phiên hoặc cuộc gọi. |
| Trạng thái, mã lỗi ngắn, thời lượng, độ trễ và các cờ hoặc mã mức cần thiết | Tổng hợp kết quả, tốc độ, hỏi lại, hủy, từ chối ảnh, tra web và cảnh báo. |
| Phiên bản thành phần được đo | So sánh lỗi và độ trễ giữa các phiên bản để phát hiện hồi quy. |

Có thể dùng `attempt_started`, `attempt_finished`, `session_started`, `session_ended` và `alert_raised` làm tên sự kiện nếu dự án chưa có quy ước. Khi gửi lại một lô, giữ nguyên mã sự kiện. Chặn ghi trùng ở AB và xử lý callback lặp theo cùng mã lượt hoặc `request_id` để mỗi lượt chỉ có một kết quả cuối. Ghép các bản ghi đến lệch thứ tự theo mã liên kết và thời điểm phát sinh.

Chỉ nhận các trường vận hành đã định nghĩa. Danh sách dữ liệu cấm ghi gồm câu nói, ảnh, nội dung OCR, số tiền nhận diện, tên người thân, số điện thoại, nội dung tin nhắn, điểm đến, tên bài hát và các nội dung riêng tư khác. Giữ nội dung yêu cầu, callback gốc và token ngoài sự kiện, log và phản hồi thống kê.

## API và phân quyền

| API | Phạm vi truy vấn và dữ liệu trả về |
| --- | --- |
| `/me/stats` | Lấy chủ kính từ danh tính đã xác thực và lọc theo chính chủ trước khi tổng hợp. Trả xu hướng sử dụng cá nhân theo ngày hoặc theo chức năng, gồm thời lượng kết nối, số lượt, kết quả và số lần thử lại, thời lượng bật cảnh báo và số lần gọi người thân. |
| `/admin/stats` | Yêu cầu token admin của AB. Trả số liệu gộp toàn hệ thống theo thời gian, chức năng hoặc phiên bản, gồm mức sử dụng, kết quả, lỗi, tốc độ và mức độ gắn bó. Phản hồi chỉ gồm các nhóm tổng hợp và giá trị thống kê. Mã chủ kính, mã kính và số liệu theo cá nhân chỉ dùng nội bộ để tính toán. |

Đề xuất dùng chung bộ lọc kỳ báo cáo, múi giờ và mã chức năng cho hai API. Khi báo cáo pilot, đối chiếu số người dùng, tỷ lệ hoàn thành và độ trễ. Đối chiếu lượt dùng với lỗi, thử lại hoặc hỏi lại khi ưu tiên cải tiến. Dùng giờ cao điểm, thời lượng và tỷ lệ cần tra web để ước tính tải và chi phí API. Dùng tín hiệu SOS và cảnh báo để ưu tiên các tình huống cần kiểm thử an toàn.

## Thứ tự triển khai đề xuất

1. Xác minh nhánh, ánh xạ chủ kính và luồng xác thực. Tạo phần nhận lô ở AB, bảng lưu sự kiện và khóa chống trùng.
2. Đi hết một luồng `ocr` từ lúc gọi đến lúc phát xong rồi đọc số liệu ở cả hai API. Đối soát một ca chụp lại để kiểm tra quy tắc lượt và tác vụ.
3. Gắn mốc callback cho hành động điện thoại và SOS. Gắn mốc đầu cuối cho các phiên live, kết nối và gọi Internet. Sau đó thêm điểm đo cho các chức năng còn lại theo bảng chỉ số.
4. Bổ sung các phép tổng hợp còn lại và đối soát số liệu từ sự kiện gốc đến từng API bằng các kịch bản dưới đây.

## Kịch bản nghiệm thu

- **Chụp lại.** Một lượt OCR bị từ chối ảnh và còn chờ chụp lại. Sau 40 giây, lượt thứ hai thành công và phát xong lời đáp. Kết quả là 2 lượt, 1 ảnh bị từ chối, 1 tác vụ thành công, 1 lần thử lại và tỷ lệ hoàn thành lần đầu bằng 0% cho bộ dữ liệu này.
- **Biên ghép lượt.** Lượt cùng luồng ở đúng giây thứ 60 được ghép khi lượt trước còn mở. Lượt ở giây thứ 61 hoặc lượt gọi sau một kết quả đã chốt mở tác vụ mới. Hai yêu cầu có ngữ cảnh độc lập được tính là hai tác vụ dù cùng chủ kính, cùng chức năng và chỉ cách nhau vài giây.
- **Xác nhận kết quả.** Khi đã có `Reply` và đang phát tiếng, tác vụ vẫn chờ chốt. Khi hành động điện thoại đang chờ callback, lượt vẫn ở trạng thái chờ. Với chức năng dùng cả hai, kết quả thành công được chốt sau khi đủ hai xác nhận. Callback báo lỗi ghi đúng mã lỗi. Callback lặp giữ nguyên số lượt và số kết quả đã chốt.
- **Hỏi lại, hủy và SOS.** Một yêu cầu có hỏi lại rồi bị hủy trong cửa sổ cho phép đi vào đúng hai chỉ số tương ứng. Một lượt SOS có xác nhận được ghi thời gian từ lúc kích hoạt tới lúc xác nhận. Một lượt SOS có lỗi đi vào nhóm thất bại.
- **Gửi lô và dữ liệu đến muộn.** Gửi lại cùng lô hoặc giao sự kiện kết thúc trước sự kiện bắt đầu vẫn cho cùng kết quả sau khi đủ dữ liệu. Tác vụ bắt đầu lúc 23:59 và chốt lúc 00:01 cập nhật tỷ lệ hoàn thành của ngày bắt đầu. Lượt thiếu mốc phát tiếng đầu vẫn được đếm và nằm ngoài tập mẫu p50/p90.
- **Phiên qua ngày.** Phiên cảnh báo từ 23:50 đến 00:10 được tính 20 phút, chia 10 phút cho mỗi ngày theo múi giờ báo cáo. Nếu phiên phát 2 cảnh báo thì kỳ chứa toàn bộ phiên có 6 cảnh báo mỗi giờ bật.
- **Phân quyền và dữ liệu.** Tạo dữ liệu cho hai chủ kính. Mỗi chủ chỉ thấy số của mình qua `/me/stats`. Admin thấy số gộp và payload chỉ có các trường thống kê. Kiểm tra lô gửi, bản ghi PostgreSQL, log và phản hồi API để bảo đảm chỉ có trường vận hành được phép.
