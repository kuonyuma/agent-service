import json

from agent_service.api.sse import encode_sse
from agent_service.services.events import RunStreamEvent


def test_encode_sse_uses_named_event_and_json_data():
    frame = encode_sse(
        RunStreamEvent(
            type="text.delta",
            sequence=3,
            data={"run_id": "run-1", "text": "你好\n世界"},
        )
    ).decode()

    assert frame.startswith("id: 3\nevent: text.delta\ndata: ")
    assert frame.endswith("\n\n")
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: ")) == {
        "run_id": "run-1",
        "text": "你好\n世界",
    }
