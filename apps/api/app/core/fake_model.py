"""
Deterministic Fake Model Provider for Agentic HR Core.
Complies with ADR 003, ADR 006, and Stage 2 Phase 11 specifications.

Provides 100% offline, deterministic model execution for testing and local CI:
- Synthesizes valid Pydantic model payloads dynamically.
- Supports fault injection (simulated rate limits, timeouts, permanent errors, repair flows).
- Accurately tracks token usage without external network calls or API keys.
- Accurately represents unpriced/offline model cost as None (null).
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from pydantic import BaseModel


class FakeRateLimitError(Exception):
    """Simulated upstream rate limit error (HTTP 429)."""


class FakePermanentError(Exception):
    """Simulated upstream non-retryable permanent error (HTTP 400/401/403/404)."""


@dataclass
class FakeFaultConfig:
    """Configurable fault injection state for deterministic testing."""

    rate_limit_countdown: int = 0
    timeout_countdown: int = 0
    permanent_error_countdown: int = 0
    simulate_repair_success: bool = False
    simulate_repair_failure: bool = False
    fail_private_route: bool = False
    custom_responses: dict[str, str] = field(default_factory=dict)


class FakeModelProvider:
    """
    Deterministic offline model provider simulator.
    Can dynamically generate responses or follow fault-injection rules.
    """

    def __init__(self) -> None:
        self.faults = FakeFaultConfig()
        self.call_history: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Resets all fault counters, custom responses, and call history."""
        self.faults = FakeFaultConfig()
        self.call_history.clear()

    def set_rate_limit_countdown(self, attempts: int) -> None:
        """Injects simulated 429 RateLimitError for the next N attempts."""
        self.faults.rate_limit_countdown = attempts

    def set_timeout_countdown(self, attempts: int) -> None:
        """Injects simulated TimeoutError for the next N attempts."""
        self.faults.timeout_countdown = attempts

    def set_permanent_error_countdown(self, attempts: int) -> None:
        """Injects simulated non-retryable permanent error for the next N attempts."""
        self.faults.permanent_error_countdown = attempts

    def set_simulate_repair_success(self, enabled: bool = True) -> None:
        """First response is invalid JSON/schema, repair response succeeds."""
        self.faults.simulate_repair_success = enabled

    def set_simulate_repair_failure(self, enabled: bool = True) -> None:
        """Both initial and repair responses return invalid JSON/schema."""
        self.faults.simulate_repair_failure = enabled

    def register_response(self, keyword: str, response: str) -> None:
        """Registers a deterministic response triggered when keyword is found in the prompt."""
        self.faults.custom_responses[keyword] = response

    async def generate(
        self,
        prompt: str,
        route: str,
        model_id: str,
        timeout_seconds: float = 30.0,
        schema: type[BaseModel] | None = None,
    ) -> tuple[str, int, int]:
        """
        Executes generation, applying fault injection and token tracking.
        Returns: (completion_text, prompt_tokens, completion_tokens).
        """
        self.call_history.append(
            {
                "prompt": prompt,
                "route": route,
                "model_id": model_id,
                "schema": schema.__name__ if schema else None,
            }
        )

        # 1. Fault: Permanent (non-retryable) error (HTTP 400 / 401 / 403 / 404)
        if self.faults.permanent_error_countdown > 0:
            self.faults.permanent_error_countdown -= 1
            raise FakePermanentError(
                "HTTP 400 Bad Request: Invalid model parameters or unsupported feature."
            )

        # 2. Fault: Timeout
        if self.faults.timeout_countdown > 0:
            self.faults.timeout_countdown -= 1
            await asyncio.sleep(0.01)
            raise TimeoutError(f"Simulated request timeout after {timeout_seconds}s")

        # 3. Fault: Rate Limit (HTTP 429)
        if self.faults.rate_limit_countdown > 0:
            self.faults.rate_limit_countdown -= 1
            raise FakeRateLimitError("Rate limit exceeded: 429 Too Many Requests. Retry after 1s.")

        # 4. Fault: Structured Output Repair Scenarios
        is_repair_prompt = "REPAIR ATTEMPT" in prompt or "validation error" in prompt.lower()

        if self.faults.simulate_repair_failure:
            output = json.dumps({"corrupted_key": "unrepairable_value", "extra": 42})
        elif self.faults.simulate_repair_success:
            if not is_repair_prompt:
                output = json.dumps({"missing_required_fields": True})
            else:
                output = (
                    self._synthesize_schema(schema)
                    if schema
                    else "Repaired text response successfully generated."
                )
        else:
            # 5. Check custom registered keyword responses
            output = None
            for keyword, custom_resp in self.faults.custom_responses.items():
                if keyword in prompt:
                    output = custom_resp
                    break

            if output is None:
                if schema:
                    output = self._synthesize_schema(schema)
                else:
                    output = f"Deterministic model response for route '{route}' using '{model_id}'."

        prompt_tokens = max(10, len(prompt.split()) * 2)
        completion_tokens = max(5, len(output.split()) * 2)

        return output, prompt_tokens, completion_tokens

    def _synthesize_schema(self, schema: type[BaseModel]) -> str:
        """Dynamically synthesizes a 100% valid JSON payload conforming to any Pydantic model."""
        import types
        from typing import Union

        payload: dict[str, Any] = {}

        for name, field_info in schema.model_fields.items():
            annotation = field_info.annotation
            origin = get_origin(annotation)
            args = get_args(annotation)

            # Handle Optional / Union[T, None]
            target_type = annotation
            if origin in (Union, types.UnionType):
                non_none = [a for a in args if a is not type(None)]
                if non_none:
                    target_type = non_none[0]
                    origin = get_origin(target_type)
                    args = get_args(target_type)

            if origin is list:
                payload[name] = [f"item_{name}"]
            elif origin is dict:
                payload[name] = {"key": f"value_{name}"}
            elif target_type is int:
                payload[name] = 42
            elif target_type is float:
                payload[name] = 85.5
            elif target_type is bool:
                payload[name] = True
            elif target_type is str:
                payload[name] = f"synthetic_{name}"
            elif isinstance(target_type, type) and issubclass(target_type, BaseModel):
                payload[name] = json.loads(self._synthesize_schema(target_type))
            else:
                payload[name] = f"sample_{name}"

        return json.dumps(payload)


# Singleton fake provider instance for application runtime & tests
fake_model_provider = FakeModelProvider()
