"""call_tool через настоящий SDK и мок-транспорт: ловит параметры, которые SDK
не принимает (TypeError), и его локальные отказы (ValueError) — без сети."""

import json

import anthropic
import httpx2

from app.llm.client import call_tool


def sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def test_call_tool_request_accepted_by_sdk():
    sent = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.update(json.loads(request.content))
        body = sse(
            {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
             "model": "x", "content": [], "stop_reason": None, "stop_sequence": None,
             "usage": {"input_tokens": 1, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "t", "name": "save", "input": {}}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"ok": true}'}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
             "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        )
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = anthropic.Anthropic(
        api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    )
    out = call_tool(
        api_key="test", model="claude-sonnet-5-5", system="s", user="u", tool_name="save",
        tool_description="d", input_schema={"type": "object"},
        max_tokens=32000,  # как в generate_listings: без стрима SDK падает ValueError
        client=client,
    )
    assert out == {"ok": True}
    assert not {"temperature", "top_p", "top_k"} & set(sent)
