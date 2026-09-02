from __future__ import annotations

import pytest

from kama_claude.core.config import KamaConfig, _apply_toml


# 功能：验证 Streamable HTTP MCP 配置解析 URL、普通 header 与环境变量 header 映射
# 设计：直接调用 TOML 应用边界，断言结构化配置完整保留且不需要真实网络连接
def test_streamable_http_mcp_config() -> None:
    config = KamaConfig()

    _apply_toml(
        config,
        {
            "mcp": {
                "servers": [
                    {
                        "name": "remote",
                        "transport": "streamable_http",
                        "url": "https://example.test/mcp",
                        "headers": {"X-Tenant": "demo"},
                        "headers_env": {"Authorization": "MCP_AUTH_HEADER"},
                    }
                ]
            }
        },
    )

    server = config.mcp.servers[0]
    assert server.url == "https://example.test/mcp"
    assert server.headers == {"X-Tenant": "demo"}
    assert server.headers_env == {"Authorization": "MCP_AUTH_HEADER"}


# 功能：验证旧的 raw TCP transport 被配置层明确拒绝并给出迁移方向
# 设计：使用最小旧配置断言 SystemExit 文本包含 streamable_http，避免运行期静默跳过 server
def test_raw_tcp_mcp_config_has_migration_error() -> None:
    with pytest.raises(SystemExit, match="streamable_http"):
        _apply_toml(
            KamaConfig(),
            {"mcp": {"servers": [{"name": "legacy", "transport": "tcp"}]}},
        )
