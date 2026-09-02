from __future__ import annotations

from mcp import types

from kama_claude.core.mcp.client import _convert_call_result


# 功能：验证 MCP text、image 和 structuredContent 同时保留为文本摘要与 LLM 富内容
# 设计：直接构造官方 SDK CallToolResult，避免网络干扰并覆盖三种结果通道的映射契约
def test_convert_call_result_preserves_rich_content() -> None:
    sdk_result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="done"),
            types.ImageContent(type="image", data="YWJj", mimeType="image/png"),
        ],
        structuredContent={"count": 1},
    )

    result = _convert_call_result(sdk_result)

    assert not result.is_error
    assert "done" in result.content
    assert "image/png" in result.content
    assert '"count": 1' in result.content
    assert result.llm_content is not None
    assert result.llm_content[1]["type"] == "image"


# 功能：验证 MCP isError 被保留，且无内容错误仍生成可诊断文本
# 设计：构造 content 为空的错误结果，防止 wrapper 把协议层失败误判为成功或返回空提示
def test_convert_call_result_preserves_is_error() -> None:
    result = _convert_call_result(types.CallToolResult(content=[], isError=True))

    assert result.is_error
    assert "no content" in result.content
