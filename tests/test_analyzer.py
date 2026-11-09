import json
from unittest.mock import Mock

import httpx
import pytest
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from assignment_analyzer import INSTRUCTIONS, AssignmentAnalyzer
from config import DEFAULT_GEMINI_MODEL
from errors import ProviderError
from models import Analysis


@pytest.fixture(autouse=True)
def retry_sleep(monkeypatch):
    sleeper = Mock()
    monkeypatch.setattr("assignment_analyzer.time.sleep", sleeper)
    return sleeper


def gemini_response(text, finish_reason="STOP"):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=finish_reason,
                content=types.Content(role="model", parts=[types.Part(text=text)]),
            )
        ]
    )


@pytest.mark.parametrize(
    "category,is_assessment,kind",
    [
        ("definite", True, "Klassenarbeit"),
        ("possible", True, "Präsentation"),
        ("homework", False, "Hausaufgabe"),
    ],
)
def test_structured_classification(assignment, analysis, category, is_assessment, kind):
    expected = analysis.model_copy(
        update={
            "category": category,
            "is_assessment": is_assessment,
            "assessment_type": kind,
        }
    )
    client = Mock()
    client.models.generate_content.return_value = gemini_response(expected.model_dump_json())
    assert AssignmentAnalyzer("fake", client=client).analyze(assignment) == expected
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == DEFAULT_GEMINI_MODEL
    assert kwargs["config"].response_json_schema == Analysis.model_json_schema()
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].automatic_function_calling.disable is True
    payload = json.loads(kwargs["contents"])
    assert payload["coursework"]["description"] == assignment.raw["description"]
    assert payload["due_at_berlin"].startswith("2026-11-20T15:00")
    assert "points alone" in INSTRUCTIONS
    assert "UNTRUSTED" in INSTRUCTIONS


@pytest.mark.parametrize(
    "update",
    [
        {"confidence": 101},
        {"confidence": -1},
        {"confidence": "95"},
        {"relevant_date": "2026-02-30"},
        {"is_assessment": False},
        {"category": "exam"},
    ],
)
def test_invalid_analysis_rejected(analysis, update):
    with pytest.raises(ValidationError):
        Analysis.model_validate(analysis.model_dump() | update)


@pytest.mark.parametrize("finish_reason", ["SAFETY", "MAX_TOKENS", "RECITATION", None])
def test_refusal_and_incomplete_are_retried_later(assignment, analysis, finish_reason):
    client = Mock()
    client.models.generate_content.return_value = gemini_response(
        analysis.model_dump_json(), finish_reason
    )
    with pytest.raises(ProviderError, match="declined"):
        AssignmentAnalyzer("fake", client=client).analyze(assignment)


def test_blocked_prompt_with_no_candidates(assignment):
    client = Mock()
    client.models.generate_content.return_value = types.GenerateContentResponse()
    with pytest.raises(ProviderError, match="declined"):
        AssignmentAnalyzer("fake", client=client).analyze(assignment)


@pytest.mark.parametrize("text", ["", "not JSON", '{"is_assessment":true}', "[]", "null"])
def test_empty_or_malformed_response_is_a_safe_failure(assignment, text):
    client = Mock()
    client.models.generate_content.return_value = gemini_response(text)
    with pytest.raises(ProviderError, match="Gemini"):
        AssignmentAnalyzer("fake", client=client).analyze(assignment)


def test_invalid_response_values_are_not_coerced(assignment, analysis):
    client = Mock()
    invalid = analysis.model_dump() | {"confidence": "95"}
    client.models.generate_content.return_value = gemini_response(json.dumps(invalid))
    with pytest.raises(ProviderError, match="invalid"):
        AssignmentAnalyzer("fake", client=client).analyze(assignment)


@pytest.mark.parametrize("fence", ["```json\n", "```\n"])
def test_full_json_fence_is_accepted(assignment, analysis, fence):
    client = Mock()
    client.models.generate_content.return_value = gemini_response(
        fence + analysis.model_dump_json() + "\n```"
    )
    assert AssignmentAnalyzer("fake", client=client).analyze(assignment) == analysis


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "rejected"),
        (401, "GEMINI_API_KEY"),
        (403, "denied"),
        (404, "unavailable or retired"),
        (429, "quota or rate limit"),
        (503, "temporarily unavailable"),
        (504, "504 DEADLINE_EXCEEDED"),
    ],
)
def test_provider_errors_are_actionable_and_sanitized(assignment, status, expected):
    client = Mock()
    client.models.generate_content.side_effect = errors.APIError(
        status, {"error": {"message": "SECRET"}}
    )
    with pytest.raises(ProviderError, match=expected) as exc:
        AssignmentAnalyzer("fake", client=client).analyze(assignment)
    assert "SECRET" not in str(exc.value)


def test_network_failure_is_sanitized(assignment):
    client = Mock()
    client.models.generate_content.side_effect = httpx.ReadTimeout("SECRET")
    with pytest.raises(ProviderError, match="network") as exc:
        AssignmentAnalyzer("fake", client=client).analyze(assignment)
    assert "SECRET" not in str(exc.value)


def test_fingerprint_changes_for_content_and_model(assignment):
    first = assignment.fingerprint("model1")
    assert first != assignment.fingerprint("model2")
    assignment.material_text.append({"status": "read", "text": "Exam announced"})
    assert first != assignment.fingerprint("model1")


def test_installed_sdk_structured_output_contract(assignment, analysis):
    requests_seen = []

    def respond(request):
        payload = json.loads(request.content)
        requests_seen.append((request.url, payload))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": analysis.model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    with genai.Client(
        api_key="offline-test",
        vertexai=False,
        enterprise=False,
        http_options=types.HttpOptions(
            httpx_client=httpx.Client(transport=httpx.MockTransport(respond))
        ),
    ) as sdk:
        assert AssignmentAnalyzer("unused", client=sdk).analyze(assignment) == analysis
    url, body = requests_seen[0]
    assert f"models/{DEFAULT_GEMINI_MODEL}:generateContent" in str(url)
    generation = body["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseJsonSchema"]["additionalProperties"] is False
    assert "systemInstruction" in body


def test_client_selects_developer_api_timeout_and_bounded_retries(monkeypatch):
    factory = Mock()
    monkeypatch.setattr("assignment_analyzer.genai.Client", factory)
    with AssignmentAnalyzer("offline-key", "custom-gemini"):
        pass
    kwargs = factory.call_args.kwargs
    assert kwargs["vertexai"] is False
    assert kwargs["enterprise"] is False
    assert kwargs["http_options"].timeout == 120000
    assert kwargs["http_options"].retry_options.attempts == 1
    factory.return_value.close.assert_called_once()


def test_server_timeout_then_success_uses_backoff(assignment, analysis, retry_sleep, caplog):
    client = Mock()
    client.models.generate_content.side_effect = [
        errors.ServerError(504, {"error": {"status": "DEADLINE_EXCEEDED", "message": "SECRET"}}),
        errors.ServerError(504, {"error": {"status": "DEADLINE_EXCEEDED", "message": "SECRET"}}),
        gemini_response(analysis.model_dump_json()),
    ]
    assert AssignmentAnalyzer("fake", client=client).analyze(assignment) == analysis
    assert client.models.generate_content.call_count == 3
    assert [call.args[0] for call in retry_sleep.call_args_list] == [5, 10]
    assert "attempt 1/3" in caplog.text
    assert "attempt 2/3" in caplog.text
    assert "SECRET" not in caplog.text


def test_persistent_timeout_stops_after_configured_attempts(assignment, retry_sleep):
    client = Mock()
    client.models.generate_content.side_effect = errors.ServerError(
        504, {"error": {"message": "SECRET"}}
    )
    with pytest.raises(ProviderError, match="after 2 attempt"):
        AssignmentAnalyzer("fake", client=client, max_attempts=2).analyze(assignment)
    assert client.models.generate_content.call_count == 2
    retry_sleep.assert_called_once_with(5)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(assignment, status, retry_sleep):
    client = Mock()
    client.models.generate_content.side_effect = errors.ClientError(
        status, {"error": {"message": "SECRET"}}
    )
    with pytest.raises(ProviderError):
        AssignmentAnalyzer("fake", client=client).analyze(assignment)
    assert client.models.generate_content.call_count == 1
    retry_sleep.assert_not_called()


def test_network_timeout_then_success_is_retried(assignment, analysis, retry_sleep):
    client = Mock()
    client.models.generate_content.side_effect = [
        httpx.ReadTimeout("SECRET"),
        gemini_response(analysis.model_dump_json()),
    ]
    assert AssignmentAnalyzer("fake", client=client).analyze(assignment) == analysis
    retry_sleep.assert_called_once_with(5)


def test_real_sdk_504_recovers_and_sends_extended_deadline(assignment, analysis, retry_sleep):
    requests_seen = []

    def respond(request):
        requests_seen.append(request)
        if len(requests_seen) == 1:
            return httpx.Response(
                504,
                json={"error": {"code": 504, "status": "DEADLINE_EXCEEDED", "message": "SECRET"}},
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": analysis.model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    with genai.Client(
        api_key="offline-test",
        vertexai=False,
        enterprise=False,
        http_options=types.HttpOptions(
            timeout=120_000,
            retry_options=types.HttpRetryOptions(attempts=1),
            httpx_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ),
    ) as sdk:
        assert AssignmentAnalyzer("unused", client=sdk).analyze(assignment) == analysis
    assert len(requests_seen) == 2
    assert all(request.headers["X-Server-Timeout"] == "120" for request in requests_seen)
    assert all(request.extensions["timeout"]["read"] == 120 for request in requests_seen)
    retry_sleep.assert_called_once_with(5)
