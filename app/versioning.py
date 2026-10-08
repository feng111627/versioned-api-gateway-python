"""接口版本协商。

通过 ``X-API-Version`` 请求头指定版本（取值为版本日期，如 ``2025-06-01``）；
缺省时使用默认（最新稳定）版本，且响应头回写实际生效版本，
保证老版本客户端的行为不会被新版本悄悄影响。

* 未知版本 -> 426 + ``version_unknown``
* 已下线版本 -> 426 + ``version_retired``（与未知版本区分）
* 参数格式错误属于 400 + ``invalid_request``，与版本问题区分
* 上游不可用属于 503 + ``upstream_unavailable``
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import CODE_VERSION_RETIRED, CODE_VERSION_UNKNOWN, VersionError

VERSION_HEADER = "x-api-version"

# 版本日期 -> 枚举值；新增版本时在此追加，老版本保持不动
V1 = "2024-01-01"
V2 = "2025-06-01"

# 已下线版本 -> 下线说明
RETIRED_VERSIONS: dict[str, str] = {
    "2023-01-01": "retired on 2024-06-01; minimum supported version is 2024-01-01",
}


class ApiVersion(str, Enum):
    """受支持的 API 版本。"""

    v1 = V1
    v2 = V2

    @property
    def label(self) -> str:
        return self.value


SUPPORTED_VERSIONS = [v.value for v in ApiVersion]
DEFAULT_VERSION = ApiVersion.v2  # 缺省请求头时使用最新稳定版


@dataclass(frozen=True)
class VersionNegotiationResult:
    version: ApiVersion
    requested: str | None  # 客户端原始请求值，缺省为 None
    explicit: bool  # 是否显式指定


def negotiate_version(requested: str | None) -> VersionNegotiationResult:
    """根据请求头协商版本。

    - ``None`` 或空串：使用默认版本；
    - 命中受支持版本：返回该版本；
    - 命中已下线版本：抛 ``version_retired``；
    - 其它取值：抛 ``version_unknown``。
    """
    if requested is None or requested.strip() == "":
        return VersionNegotiationResult(DEFAULT_VERSION, requested, explicit=False)

    raw = requested.strip()

    for member in ApiVersion:
        if member.value == raw:
            return VersionNegotiationResult(member, raw, explicit=True)

    if raw in RETIRED_VERSIONS:
        raise VersionError(
            CODE_VERSION_RETIRED,
            f"API version {raw} has been retired",
            {"requested": raw, "retired_reason": RETIRED_VERSIONS[raw],
             "supported": SUPPORTED_VERSIONS},
        )

    raise VersionError(
        CODE_VERSION_UNKNOWN,
        f"unknown API version: {raw}",
        {"requested": raw, "supported": SUPPORTED_VERSIONS,
         "default": DEFAULT_VERSION.value},
    )
