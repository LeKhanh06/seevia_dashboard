# contact_sms (SIM) - AB + app

File đổi: server/src/app/{actions,api/service,contract_api/public,schemas/service_requests,schemas/service_results}.py; alembic 20261021_0012_contact_sms_action.py (mới, nới CHECK operation/action);
contracts/{contract-matrix.md,public-api.openapi.yaml,device-api.openapi.yaml} (xuất lại); scripts/{phone_sim,real_client_e2e}.py; tests server (actions, service_api, openapi_contracts, service_requests, service_results);
mobile: CommandModels.kt (ActionType.CONTACT_SMS), ActionHandler.kt, CallActions.kt (ContactSmsHandler), ActivityLogUiState.kt, FunctionTestModels.kt, ActionRegistryTest.kt; docs/MOBILE_APP_PROTOCOL.md (13->14).
Kết quả: server 353 pass / 82 skip; gradle testDebugUnitTest + assembleDebug OK.
Lệch brief: cần migration Alembic (CHECK constraint); lệnh SMS chạy lane SLOW như contact_call; request_id/device_id là trường chung (không có glasses_device_id).
Lưu ý triển khai: phải chạy `alembic upgrade head` trên DB thật trước khi dùng contact/sms.
