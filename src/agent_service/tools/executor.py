from google.genai import types

from agent_service.tools.index import find_tool


async def execute_tools(function_calls: list[types.FunctionCall]) -> types.Content:

    parts = []
    for fc in function_calls:
        name = fc.name or ""
        args = fc.args or {}
        t = find_tool(name)
        if t is None:
            part = types.Part.from_function_response(
                name=name, response={"result": f"未知的工具{name}"}
            )
            parts.append(part)
            continue
        response = await t.run(parameter=args)

        part = types.Part.from_function_response(
            name=name, response={"result": response.content}
        )
        parts.append(part)

    return types.Content(role="user", parts=parts)
