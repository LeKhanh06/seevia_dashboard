# Throwaway settings for a side AB on 18011 against the disposable test DB (not real secrets).
export APP_ENV=test HTTP_PORT=18011
export DATABASE_URL=postgresql+asyncpg://app_demo_test:app_demo_test@127.0.0.1:55432/app_demo_test
export PUBLIC_API_TOKEN_HASH=$(printf '1%.0s' {1..64}) PUBLIC_API_CLIENT_ID=e2e PUBLIC_API_SCOPES=service:execute
export DEVICE_API_TOKEN_HASH=$(printf '2%.0s' {1..64}) FIELD_ENCRYPTION_KEY=e2e-only-key DELIVERY_TRANSPORT=fake
export USAGE_API_TOKEN=e2e-usage-token
export USAGE_API_TOKEN_HASH=$(python3 -c "import hmac,hashlib;print(hmac.new(b'e2e-usage-token',b'app-demo-auth/v1/usage-api',hashlib.sha256).hexdigest())")
export USAGE_ID_KEY=$(printf 'ab%.0s' {1..32})
export ADMIN_CORS_ORIGINS='["http://127.0.0.1:18012"]'
