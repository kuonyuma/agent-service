from google.genai import types

from agent_service.services.content_mapper import (
    content_kind,
    dump_content,
    load_content,
)


def test_content_mapper_round_trips_function_response_error_flag():
    part = types.Part.from_function_response(
        name="read_file",
        response={"result": "文件不存在", "is_error": True},
    )
    assert part.function_response is not None
    part.function_response.id = "call-1"
    original = types.Content(role="user", parts=[part])

    restored = load_content(dump_content(original))

    assert content_kind(restored) == "tool_result"
    assert restored.parts is not None
    response = restored.parts[0].function_response
    assert response is not None
    assert response.id == "call-1"
    assert response.response == {"result": "文件不存在", "is_error": True}
