# versioned-api-gateway

基于 FastAPI 的统一入口接口网关：对外提供**版本协商**、**幂等写**、**按调用方限流**，
并把所有错误收敛为稳定、可解释的对外结构。

## 快速开始

```bash
uv sync                 # 安装依赖
uv run python main.py   # 启动服务（127.0.0.1:8000）
uv run pytest           # 运行全部自动化测试
```

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查（不限流） |
| POST | `/orders` | 创建订单（支持幂等键） |
| GET | `/orders/{id}` | 按协商版本读取订单 |

通用请求头：`X-API-Version`（版本）、`Idempotency-Key`（幂等键，仅写操作）、
`X-Caller-Id`（调用方身份，限流维度）、`X-Request-Id`（链路追踪，缺省自动生成）。

## 版本策略

- 版本通过 `X-API-Version` 请求头指定；**缺省按 `v1` 处理**，保证未升级的老客户端
  行为不被新版本悄悄改变。
- 当前支持 `v1`、`v2`；`v0` 已下线。内部始终以最新版（v2）为规范表示存储，
  老版本由网关做**降级转换**（响应）与**升级转换**（请求），互不影响。
- 字段差异示例（订单金额）：
  - `v1`：`total` 浮点数（主单位，如 `12.99`），无货币字段；
  - `v2`：`total_cents` 整数（最小单位，如 `1299`）+ `currency`。
- 版本问题的拒绝与参数错误、服务不可用**明确区分**：

| 情况 | HTTP | `error.code` |
| --- | --- | --- |
| 版本不存在 | 426 | `unsupported_version` |
| 版本已下线 | 410 | `version_retired` |
| 参数/校验错误 | 400 | `invalid_request` |
| 服务不可用 | 503 | `service_unavailable` |

## 幂等语义

- 写操作携带 `Idempotency-Key` 后启用幂等：
  - 同键 + 相同内容（含并发同时到达）：**只真正执行一次**，后续请求重放首次的
    状态码与响应体，并带 `Idempotency-Replayed: true` 响应头；
  - 同键 + 不同内容：**409 `idempotency_conflict`** 拒绝；
  - 不同键：完全互不干扰；
  - 执行失败（如上游异常）**不会落记录**，客户端可安全重试。
- 并发保护基于每键一把异步锁，同时到达的重复提交会串行等待首个执行的结果。

## 限流规则

- 按调用方（`X-Caller-Id`，缺省 `anonymous`）独立的**令牌桶**：默认容量 20、
  每秒回充 5 个令牌（可用 `GatewayConfig` 调整）。
- 每个响应都带额度头：`X-RateLimit-Limit`、`X-RateLimit-Remaining`。
- 超限返回 **429 `rate_limited`**，响应体含 `limit`/`remaining`/`retry_after_seconds`，
  并带 `Retry-After` 头；不同调用方互不影响；`/healthz` 不限流。

## 统一错误结构

所有错误（包括框架层 404/405、未预期异常）都返回同一信封，绝不泄露内部堆栈或
第三方库异常：

```json
{
  "error": {"code": "rate_limited", "message": "...", "details": {...}},
  "request_id": "..."
}
```

`request_id` 与响应头 `X-Request-Id` 一致，可用于服务端日志定位。

## 本地验证

```bash
uv run pytest            # 全量测试；日志按场景分组输出每个用例与结果
uv run pytest tests/test_versioning.py   # 只跑版本兼容场景
uv run pytest tests/test_idempotency.py  # 只跑幂等/并发场景
uv run pytest tests/test_ratelimit.py    # 只跑限流边界场景
```

测试日志示例（`SCENARIO BEGIN` 标记场景，`CASE PASS/FAIL` 标记用例结果）：

```
[INFO] gateway.tests: SCENARIO BEGIN test_idempotency :: 幂等与并发：重复提交、并发同键、键冲突、失败可重试
[INFO] gateway.tests: CASE PASS tests/test_idempotency.py::test_concurrent_same_key_executes_once
```

手工验证（服务运行中）：

```bash
# 版本协商
curl -s -H 'X-API-Version: v2' -X POST localhost:8000/orders \
     -H 'Content-Type: application/json' -d '{"item":"book","total_cents":1299}'
curl -s -H 'X-API-Version: v9' localhost:8000/orders/ord_000001   # 426

# 幂等重放（两次请求同一幂等键，第二次带 Idempotency-Replayed 头）
curl -si -H 'Idempotency-Key: k1' -X POST localhost:8000/orders \
     -H 'Content-Type: application/json' -d '{"item":"pen","total":2.5}'

# 限流（快速连续请求超过 20 次后出现 429 与 Retry-After）
for i in $(seq 25); do curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/healthz; done
```

## 代码结构

```
app/
  main.py         # 应用工厂：装配状态、请求ID/限流中间件、异常注册
  routes.py       # /orders 端点：协商 -> 幂等 -> 存储 -> 版本化响应
  versioning.py   # 版本注册表：协商、升降级转换
  idempotency.py  # 幂等键存储：每键锁、结果重放、冲突检测
  ratelimit.py    # 按调用方的令牌桶
  store.py        # 订单内存存储（规范表示）+ 模拟上游依赖
  errors.py       # 统一错误信封与异常类型
  schemas.py      # v1/v2 请求模型
tests/            # 按场景组织的自动化测试
```
