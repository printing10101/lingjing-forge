"""Read only the authenticated local MCP tool schema; do not invoke any tool."""
import asyncio
import json
from pathlib import Path

from mcp import ClientSession
from mcp.client.sse import sse_client

directory = Path(__file__).resolve().parent


async def verify():
    token = Path("D:/nexent-deploy/mcp-token.txt").read_text(encoding="utf-8").strip()
    async with sse_client("http://127.0.0.1:8088/sse", headers={"Authorization": "Bearer " + token}, timeout=8) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.list_tools()
            tool = next(tool for tool in result.tools if tool.name == "cam_recommend_process")
            assert "conditions" in tool.inputSchema["properties"]
            return {"schema_verified": True, "tool": tool.name, "conditions_parameter": True,
                    "tool_calls": 0, "approval_tools": [tool.name for tool in result.tools if "approve" in tool.name]}


if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        report = asyncio.run(asyncio.wait_for(verify(), timeout=25))
    except Exception as error:
        report = {"schema_verified": False, "failure_type": type(error).__name__, "tool_calls": 0}
    (directory / "mcp-schema.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
