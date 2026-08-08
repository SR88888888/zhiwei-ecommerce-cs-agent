# 知微拼多多客服 MVP

新 MVP 使用双 Adapter：默认 `mock` 模式提供本地订单、物流和售后闭环；设置 `PDD_ADAPTER_MODE=live` 后，后端会经由企业网关调用真实平台能力。

## 启动

1. 复制 `apps/api/.env.example` 为 `apps/api/.env`，按需填入 DeepSeek Key。
2. 执行 `docker compose -f docker-compose.mvp.yml up --build`。
3. 打开 `http://localhost:5173`。

Mock 账号 `buyer_001` 可查询订单 `PDD20260806001`；`buyer_002` 无权查询该订单，可用于验证越权保护。
