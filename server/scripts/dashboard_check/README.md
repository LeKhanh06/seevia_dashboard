# Đối soát AB với dashboard `seevia_dashboard` (không cần Chrome)

Chạy mapper thật của dashboard (`js/registry.js` → `map`) trên phản hồi thật của AB, báo `undefined`/`NaN`/`[object`,
và kiểm lại mọi ràng buộc của `js/api/mock/validate.js` trên API thật. Chỉ dùng DB test (`compose.test.yaml`).

```bash
git clone --depth 30 https://github.com/LeKhanh06/seevia_dashboard.git /tmp/dash      # thư mục tạm
source scripts/dashboard_check/env.test.sh          # giá trị giả, chỉ cho AB phụ + DB test
export UV_PROJECT_ENVIRONMENT=/home/vannk/.venvs-local/seevia-ab
(cd /tmp && uv run --project "$OLDPWD" uvicorn app.main:app --app-dir "$OLDPWD/src" --port 18011) &   # chạy ngoài server/ để không nạp .env
PYTHONPATH=. uv run python scripts/dashboard_check/seed.py   # admin e2e@example.com / E2epass123 + bộ dữ liệu test
node scripts/dashboard_check/check_real.mjs /tmp/dash http://127.0.0.1:18011/api/v1 e2e@example.com E2epass123
```

Xong thì tắt AB phụ (`fuser -k 18011/tcp`) và TRUNCATE DB test (test tích hợp giả định bảng rỗng).
