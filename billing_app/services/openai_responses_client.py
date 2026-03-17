from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


DEFAULT_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_RESPONSES_MODEL = "gpt-5-mini"


class OpenAIResponsesError(RuntimeError):
    """Raised when the Responses API request fails or returns unusable data."""


def _extract_response_text(payload: dict[str, Any]) -> str:
    direct_text = str(payload.get("output_text", "")).strip()
    if direct_text:
        return direct_text

    for output_item in payload.get("output", []):
        if not isinstance(output_item, dict):
            continue
        if str(output_item.get("type", "")).strip() != "message":
            continue
        for content_item in output_item.get("content", []):
            if not isinstance(content_item, dict):
                continue
            if str(content_item.get("type", "")).strip() in {"output_text", "text"}:
                text_value = str(content_item.get("text", "")).strip()
                if text_value:
                    return text_value
            json_value = content_item.get("json")
            if isinstance(json_value, dict):
                return json.dumps(json_value, ensure_ascii=True)
    return ""


def create_structured_response(
    *,
    instructions: str,
    input_payload: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
    schema_description: str,
    metadata: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    max_output_tokens: int = 700,
) -> dict[str, Any]:
    api_key = str(os.environ.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        raise OpenAIResponsesError("Configura OPENAI_API_KEY en el entorno del servidor para usar el asistente de IA.")

    endpoint_url = str(os.environ.get("OPENAI_RESPONSES_URL", DEFAULT_RESPONSES_URL)).strip() or DEFAULT_RESPONSES_URL
    model = str(os.environ.get("OPENAI_RESPONSES_MODEL", DEFAULT_RESPONSES_MODEL)).strip() or DEFAULT_RESPONSES_MODEL
    request_body: dict[str, Any] = {
        "model": model,
        "store": False,
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(input_payload, ensure_ascii=True),
                    }
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "description": schema_description,
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": max_output_tokens,
        "metadata": metadata or {},
    }
    if tools:
        request_body["tools"] = tools
    if tool_choice is not None:
        request_body["tool_choice"] = tool_choice

    encoded_body = json.dumps(request_body, ensure_ascii=True).encode("utf-8")
    http_request = request.Request(
        endpoint_url,
        data=encoded_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=60) as http_response:
            raw_payload = http_response.read().decode("utf-8")
    except error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(error_body)
            message = str(error_payload.get("error", {}).get("message", "")).strip()
        except json.JSONDecodeError:
            message = error_body.strip()
        raise OpenAIResponsesError(message or f"OpenAI devolvio HTTP {exc.code}.") from exc
    except error.URLError as exc:
        raise OpenAIResponsesError("No pude conectar con OpenAI Responses API desde este servidor.") from exc

    try:
        response_payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise OpenAIResponsesError("OpenAI devolvio una respuesta no valida en formato JSON.") from exc

    output_text = _extract_response_text(response_payload)
    if not output_text:
        raise OpenAIResponsesError("OpenAI no devolvio texto util para esta accion.")

    try:
        structured_output = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise OpenAIResponsesError("La salida estructurada de OpenAI no llego en JSON valido.") from exc

    if not isinstance(structured_output, dict):
        raise OpenAIResponsesError("La salida estructurada de OpenAI no tiene el formato esperado.")

    return {
        "model": str(response_payload.get("model", "")).strip() or model,
        "response_id": str(response_payload.get("id", "")).strip(),
        "output": structured_output,
        "raw_response": response_payload,
    }
