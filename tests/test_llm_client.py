"""call_structured через настоящий SDK и мок-транспорт: ловит параметры, которые SDK
не принимает (TypeError), его локальные отказы (ValueError) и запрещённые моделью
поля запроса — без сети."""

import json

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from app.llm.client import call_structured


class Item(BaseModel):
    name: str
    size: int | None = None


class Out(BaseModel):
    items: list[Item]


def sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def fake_client(text: str | None, stop_reason: str, sent: dict) -> anthropic.Anthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.update(json.loads(request.content))
        block = [] if text is None else [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            {"type": "content_block_stop", "index": 0},
        ]
        body = sse(
            {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
             "model": "x", "content": [], "stop_reason": None, "stop_sequence": None,
             "usage": {"input_tokens": 1, "output_tokens": 1}}},
            *block,
            {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
             "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        )
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return anthropic.Anthropic(
        api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    )


def call(client: anthropic.Anthropic) -> Out:
    return call_structured(
        api_key="test", model="claude-sonnet-5-5", system="s", user="u", output=Out,
        max_tokens=32000,  # как в generate_listings: без стрима SDK падает ValueError
        client=client,
    )


def test_request_accepted_by_sdk_and_parsed():
    sent: dict = {}
    out = call(fake_client('{"items": [{"name": "a", "size": 2}]}', "end_turn", sent))
    assert out == Out(items=[Item(name="a", size=2)])

    assert not {"temperature", "top_p", "top_k", "tool_choice"} & set(sent)
    schema = sent["output_config"]["format"]["schema"]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Item"]["additionalProperties"] is False


def test_truncated_or_refused_text_raises():
    with pytest.raises(ValidationError):
        call(fake_client('{"items": [', "max_tokens", {}))
    with pytest.raises(ValidationError):
        call(fake_client("Не могу помочь.", "refusal", {}))


def test_no_content_raises():
    with pytest.raises(RuntimeError, match="refusal"):
        call(fake_client(None, "refusal", {}))
