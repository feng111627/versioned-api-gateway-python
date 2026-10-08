# Versioned API Gateway

在 FastAPI 之上构建的统一接口网关，面向不同版本的客户端，在**版本兼容、重复提交、突发流量**三类场景下提供稳定、可解释的结果。

核心能力：

1. **接口版本协商**：同一资源在不同版本下字段结构/语义不同，按版本正确返回；老版本行为不被新版本悄悄影响；未知/已下线版本明确拒绝。
2. **写操作幂等与并发保护**：同一幂等键的重复提交（含同时到达）只真正生效一次并返回一致结果；同键不同内容按冲突拒绝；不同键互不干扰。
3. **按调用方限流**：按 `X-Caller-Id` 独立计数，超限可区分地拒绝，并在响应头反映剩余额度。
4. **统一错误结构**：内部/上游异常被收敛为稳定信封，绝不透出内部堆栈或第三方库异常。

---

## 快速开始

```bash
uv sync                       # 安装依赖
uv run python main.py         # 启动服务，默认 http://127.0.0.1:8000
# 或
uv run uvicorn app.main:app --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/
# {"service":"versioned-api-gateway","status":"ok"}
```

交互式文档：启动后访问 `http://127.0.0.1:8000/docs`。

### 资源与必需请求头

| 接口 | 方法 | 必需头 |
| --- | --- | --- |
| `/orders` | POST | `X-Caller-Id`、`Idempotency-Key`、`X-API-Version`（缺省走默认版本） |
| `/orders/{id}` | GET | `X-Caller-Id`、`X-API-Version`（缺省走默认版本） |

下单请求体：`{"amount": <分，正整数>, "item": "<非空字符串>"}`。

---

## 一、版本策略

通过 **`X-API-Version`** 请求头协商版本，取值为版本日期：

| 版本 | 状态 | 说明 |
| --- | --- | --- |
| `2024-01-01`（v1） | 受支持，**冻结** | 老结构，不再改动 |
| `2025-06-01`（v2） | 受支持，**默认/最新** | 新结构 |
| `2023-01-01` | 已下线 | 返回 `version_retired` |
| 其它任意值 | 未知 | 返回 `version_unknown` |

规则：

* **缺省请求头**时使用默认版本（当前为 v2）；**显式指定 v1 的老客户端永远拿到 v1 结构**，新版本上线不会改变它们的行为。
* 每个响应都回写 **`X-API-Version`**，表明实际生效的版本（版本被拒绝时回写客户端请求值，便于排查）。
* 新增版本 = 追加一个新版本并把默认版本指向它，**老版本的序列化逻辑保持不动**。

### 同一份订单在两个版本下的差异

v1（`2024-01-01`，冻结）：

```json
{ "order_id": "ord-1", "amount": 100, "item_name": "book", "currency": "CNY" }
```

v2（`2025-06-01`，默认）：

```json
{
  "id": "ord-1",
  "amount": { "value": 10000, "currency": "CNY" },
  "item": "book",
  "created_at": "2026-10-08T00:00:00Z"
}
```

差异点：`order_id → id`、`item_name → item` 重命名；金额由「元整数平铺」改为「最小货币单位（分）的结构化对象」；v2 增加 `created_at`。

### 版本类失败与其它失败明确区分

| 情况 | HTTP 状态码 | `error.code` |
| --- | --- | --- |
| 未知版本 | 426 Upgrade Required | `version_unknown` |
| 已下线版本 | 426 Upgrade Required | `version_retired` |
| 参数错误（非法金额、缺必需头等） | 400 | `invalid_request` |
| 上游不可用 | 503 | `upstream_unavailable` |

版本错误的 `details` 中带 `supported` 列表；已下线版本还带 `retired_reason`。

---

## 二、幂等语义

写接口必须携带 **`Idempotency-Key`**（字符串）。键的作用域为「方法 + 路径 + 键」，因此不同接口、不同键互不干扰。

| 场景 | 行为 | 响应 |
| --- | --- | --- |
| 同键 + **同内容**，首次提交 | 真正执行一次上游 | `201`，响应头 `Idempotency-Replayed: false` |
| 同键 + 同内容，**重复/并发**提交 | 不再次执行，返回首次结果 | `201`，`Idempotency-Replayed: true`，**响应体与首次完全一致** |
| 同键 + **不同内容**（首次已结束后提交） | 拒绝 | `409 idempotency_conflict` |
| 同键 + 不同内容（**首次仍在处理中**到达） | 拒绝（并发冲突） | `409 idempotency_inflight` |
| 同键请求在上游执行时**失败** | 不占用幂等键，可用同键重试 | `503 upstream_unavailable`，重试成功后正常 `201` |

要点：

* **并发安全**：每个键一把锁。同键同时到达时只有一个请求进入上游执行，其余请求在锁上等待；相同内容等待后拿到首次结果，不同内容按在途冲突拒绝。
* **请求内容判定**：对规范化后的请求体（键排序）做 SHA-256，等价 JSON（键顺序不同）视为相同内容。
* **失败不缓存**：上游异常时删除占位记录，保证「只缓存确定成功的结果」，客户端可用同一幂等键安全重试。
* 本实现为**进程内存储**（`app/idempotency.py`），适合演示与单实例部署；多实例/生产可将 `IdempotencyStore` 替换为 Redis（`SET NX` + 短 TTL 占位 + 完成后写最终值），语义不变。

---

## 三、限流规则

* 按 **`X-Caller-Id`** 维度做**固定窗口**计数；不同调用方各算各的，互不影响。
* 默认配额 **每 60 秒 100 次**，可用环境变量调整：
  * `GATEWAY_RATE_LIMIT`（窗口内请求数）
  * `GATEWAY_RATE_WINDOW`（窗口秒数）
* 边界：第 `limit` 个请求仍正常处理（此时剩余为 0），第 `limit+1` 个返回 **429 `rate_limited`**。
* 窗口结束后计数自动清零、额度恢复。

所有被限流的接口响应（成功或 429）都带标准额度头：

| 响应头 | 含义 |
| --- | --- |
| `X-RateLimit-Limit` | 窗口配额 |
| `X-RateLimit-Remaining` | 当前窗口剩余额度（被限时为 0） |
| `X-RateLimit-Reset` | 窗口重置的 Unix 秒级时间戳 |
| `Retry-After` | 建议重试的等待秒数（仅 429 时有意义） |

缺少 `X-Caller-Id` 属于参数错误（`400 invalid_request`），在限流之前返回，且不消耗配额。

---

## 四、统一错误结构

任何失败都返回如下信封，便于调用方程序化处理：

```json
{
  "error": {
    "code": "rate_limited",
    "message": "rate limit exceeded for caller alice",
    "details": { }
  }
}
```

错误码总览：

| `error.code` | HTTP | 含义 |
| --- | --- | --- |
| `invalid_request` | 400 | 参数/请求头错误（含非法 JSON、缺幂等键/调用方） |
| `not_found` | 404 | 资源不存在 |
| `version_unknown` | 426 | 未知 API 版本 |
| `version_retired` | 426 | 版本已下线 |
| `idempotency_conflict` | 409 | 幂等键被不同内容复用（事后） |
| `idempotency_inflight` | 409 | 同幂等键首次请求在途且内容不同（并发） |
| `rate_limited` | 429 | 超出调用方配额 |
| `upstream_unavailable` | 503 | 上游依赖暂不可用（可重试） |
| `internal_error` | 500 | 未预期内部错误（细节仅进服务端日志） |

内部异常的完整堆栈只写入服务端日志，**响应中不包含堆栈、异常类名或第三方库原始文案**。

---

## 五、本地验证

### 运行自动化测试

```bash
uv run pytest
```

测试按四大场景组织，运行时会打印每个场景覆盖的用例及其结果，形如：

```
[INFO] 场景[版本兼容] 用例: 显式 v1 应返回老结构(...)
[INFO] 用例结果 :: PASSED :: tests/test_versioning.py::test_v1_returns_legacy_shape
```

| 测试文件 | 覆盖重点 |
| --- | --- |
| `tests/test_versioning.py` | v1/v2 字段结构、默认版本与老版本钉住、未知/已下线版本、GET 版本化、错误分类 |
| `tests/test_idempotency.py` | 顺序重复、**真实服务器真并发**（同内容只生效一次 / 不同内容在途冲突）、不同键隔离、失败重试、缺幂等键 |
| `tests/test_ratelimit.py` | 限流边界、额度头递减、调用方隔离、窗口滚动恢复、缺调用方、错误分类 |
| `tests/test_errors.py` | 统一信封、503 不泄露内部细节、404/非法 JSON 收敛、各错误码可区分 |

> 幂等并发用例会通过夹具启动一个真实的多请求 uvicorn 子进程（`tests/conftest.py` 的 `server` 夹具），
> 因为 `TestClient` 会在同一执行通道里串行化端点，无法真实复现"同时到达"。

### 手工 curl 走查

```bash
# 启动
uv run python main.py &

# 1) 版本：v1 vs v2
curl -s -X POST localhost:8000/orders \
  -H 'X-Caller-Id: demo' -H 'Idempotency-Key: k1' -H 'X-API-Version: 2024-01-01' \
  -H 'Content-Type: application/json' -d '{"amount":10000,"item":"book"}'
curl -s -X POST localhost:8000/orders \
  -H 'X-Caller-Id: demo' -H 'Idempotency-Key: k2' -H 'X-API-Version: 2025-06-01' \
  -H 'Content-Type: application/json' -d '{"amount":10000,"item":"book"}'

# 2) 幂等：同键同内容重复 -> 第二次 Idempotency-Replayed: true，订单 id 相同
curl -s -i -X POST localhost:8000/orders -H 'X-Caller-Id: demo' -H 'Idempotency-Key: same' \
  -H 'Content-Type: application/json' -d '{"amount":500,"item":"pen"}'
curl -s -i -X POST localhost:8000/orders -H 'X-Caller-Id: demo' -H 'Idempotency-Key: same' \
  -H 'Content-Type: application/json' -d '{"amount":500,"item":"pen"}'

# 3) 同键不同内容 -> 409 idempotency_conflict
curl -s -i -X POST localhost:8000/orders -H 'X-Caller-Id: demo' -H 'Idempotency-Key: same' \
  -H 'Content-Type: application/json' -d '{"amount":999,"item":"other"}'

# 4) 未知版本 -> 426 version_unknown
curl -s -i -X POST localhost:8000/orders -H 'X-Caller-Id: demo' -H 'Idempotency-Key: k3' \
  -H 'X-API-Version: 2099-01-01' -H 'Content-Type: application/json' -d '{"amount":1,"item":"x"}'
```

限流边界可用小配额快速验证：

```bash
GATEWAY_RATE_LIMIT=3 GATEWAY_RATE_WINDOW=60 uv run python main.py &
for i in 1 2 3 4; do
  curl -s -o /dev/null -w "%{http_code} remaining=%header{x-ratelimit-remaining}\n" \
    localhost:8000/orders/x -H 'X-Caller-Id: alice'
done
# 前三次 404/200(remaining 2/1/0)，第四次 429，带 Retry-After
```

上游不可用与统一错误：将下单 `item` 置为 `force-upstream-failure`，返回 503 `upstream_unavailable`，且不泄露内部异常原文。

---

## 项目结构

```
app/
  main.py          # 应用装配：版本中间件、限流依赖、幂等下单、统一异常处理
  versioning.py    # 版本注册表与协商逻辑
  schemas.py       # 请求模型与按版本的响应序列化
  idempotency.py   # 幂等存储 + 每键锁 + 每请求租约
  ratelimit.py     # 按调用方的固定窗口限流器
  upstream.py      # 模拟上游（含确定性故障/延迟注入）
  errors.py        # 错误码、错误类型与统一信封
tests/             # 四大场景的自动化测试
main.py            # 入口（app.main:app）
```
