"""
Central Model Gateway for Agentic HR Core.
Complies with ADR 003 (Model Routing & Privacy Boundary), ADR 006, and Stage 2 Phase 11 specifications.

Provides:
1. Dynamic model selection across three routes (cheap, strong, private).
2. Strict fail-closed boundary for private route on real sensitive HR data.
3. Structured output with exactly one repair attempt on the original approved route.
4. Bounded retries (maximum 3 total provider attempts: 1 initial + up to 2 retries) for transient errors only.
5. Immediate failure on permanent upstream errors without retries.
6. Unknown model cost representation strictly as None (JSON null), never falsely reported as 0.0.
7. Privacy-preserving usage-only tracing with InMemoryTraceSink for offline safe execution.
"""

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

# Configure LiteLLM for offline, low-overhead operation
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from app.core.config import settings
from app.core.fake_model import fake_model_provider
from app.core.untrusted import wrap_untrusted_text

logger = logging.getLogger("agentic_hr.model_gateway")

T = TypeVar("T", bound=BaseModel)


# -------------------------------------------------------------------------
# Routes & Exceptions
# -------------------------------------------------------------------------
class ModelRoute(str, Enum):
    """Authoritative model routing tiers for Agentic HR Core (ADR 003 & ADR 007)."""

    CHEAP = "cheap"
    STRONG = "strong"
    REASONING = "reasoning"
    PRIVATE = "private"


class ModelGatewayError(Exception):
    """Base exception for all Model Gateway operations."""


class ModelRouteUnavailableError(ModelGatewayError):
    """Raised when a requested route is unconfigured or unavailable."""


class ModelRateLimitError(ModelGatewayError):
    """Raised when upstream rate limits (HTTP 429) persist after all retries."""


class ModelTimeoutError(ModelGatewayError):
    """Raised when a model request exceeds configured connection/execution timeout."""


class ModelValidationError(ModelGatewayError):
    """Raised when model output fails Pydantic schema validation after the repair attempt."""

    def __init__(
        self,
        message: str,
        original_output: str | None = None,
        repaired_output: str | None = None,
    ) -> None:
        super().__init__(message)
        self.original_output = original_output
        self.repaired_output = repaired_output


class ModelProviderError(ModelGatewayError):
    """Raised when the upstream model provider returns a fatal or unrecoverable error."""


# -------------------------------------------------------------------------
# Metadata & Response Dataclasses
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class TokenUsage:
    """Token consumption metrics for a model interaction."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class ModelResponse(Generic[T]):
    """Unified, strongly-typed envelope for all model completions."""

    content: T | str
    usage: TokenUsage
    route: str
    model_id: str
    provider: str
    latency_ms: float
    cost_usd: float | None = None
    retry_count: int = 0
    repair_attempts: int = 0


# -------------------------------------------------------------------------
# Offline In-Memory Trace Sink
# -------------------------------------------------------------------------
class InMemoryTraceSink:
    """
    Offline in-memory trace sink.
    Captures usage-only trace dictionaries during testing and fake-mode operations
    without initializing background threads or external network calls.
    """

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def trace(
        self,
        name: str,
        metadata: dict[str, Any],
        input: str,
        output: str,
    ) -> None:
        self.traces.append(
            {
                "name": name,
                "metadata": metadata,
                "input": input,
                "output": output,
            }
        )

    def clear(self) -> None:
        self.traces.clear()


# -------------------------------------------------------------------------
# Central Model Gateway Class
# -------------------------------------------------------------------------
class ModelGateway:
    """
    Provider-neutral AI gateway with route-based model dispatch,
    bounded retries, structured output repair, and privacy-preserving tracing.
    """

    def __init__(self) -> None:
        self.timeout = settings.MODEL_GATEWAY_TIMEOUT_SECONDS
        self.max_retries = (
            settings.MODEL_GATEWAY_MAX_RETRIES
        )  # Default 2 retries (3 attempts total)
        self.base_delay = settings.MODEL_GATEWAY_RETRY_BASE_DELAY
        self.trace_sink = InMemoryTraceSink()
        self._langfuse_client = None
        self._init_langfuse()

    def _init_langfuse(self) -> None:
        """
        Initializes Langfuse client ONLY when explicitly enabled and not in fake mode.
        When USE_FAKE_INTEGRATIONS is True, strictly uses InMemoryTraceSink to prevent network calls.
        """
        if settings.USE_FAKE_INTEGRATIONS or not settings.LANGFUSE_TRACING_ENABLED:
            self._langfuse_client = None
            return

        if settings.LANGFUSE_PUBLIC_KEY:
            try:
                from langfuse import Langfuse

                self._langfuse_client = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_BASE_URL,
                )
                logger.info("Langfuse monitoring client initialized.")
            except Exception as exc:
                logger.warning(f"Failed to initialize Langfuse: {exc}")
                self._langfuse_client = None

    def resolve_route(self, route: str | ModelRoute) -> tuple[str, str, str | None, str | None]:
        """
        Resolves provider, model ID, API key, and optional endpoint for a given route.
        Enforces strict fail-closed boundary for the 'private' route.
        """
        route_str = str(route.value if isinstance(route, ModelRoute) else route).lower().strip()

        if route_str == ModelRoute.CHEAP.value:
            if settings.USE_FAKE_INTEGRATIONS:
                provider = settings.CHEAP_MODEL_PROVIDER or "fake"
                model_id = settings.CHEAP_MODEL_ID or "fake-cheap-model"
            else:
                if not settings.CHEAP_MODEL_PROVIDER or not settings.CHEAP_MODEL_ID:
                    raise ModelRouteUnavailableError(
                        "Cheap model route is not configured in environment "
                        "(CHEAP_MODEL_PROVIDER or CHEAP_MODEL_ID missing)."
                    )
                provider = settings.CHEAP_MODEL_PROVIDER
                model_id = settings.CHEAP_MODEL_ID
            api_key = settings.CHEAP_MODEL_API_KEY
            endpoint = None

        elif route_str == ModelRoute.STRONG.value:
            if settings.USE_FAKE_INTEGRATIONS:
                provider = settings.STRONG_MODEL_PROVIDER or "fake"
                model_id = settings.STRONG_MODEL_ID or "fake-strong-model"
            else:
                if not settings.STRONG_MODEL_PROVIDER or not settings.STRONG_MODEL_ID:
                    raise ModelRouteUnavailableError(
                        "Strong model route is not configured in environment "
                        "(STRONG_MODEL_PROVIDER or STRONG_MODEL_ID missing)."
                    )
                provider = settings.STRONG_MODEL_PROVIDER
                model_id = settings.STRONG_MODEL_ID
            api_key = settings.STRONG_MODEL_API_KEY
            endpoint = None

        elif route_str == ModelRoute.REASONING.value:
            if settings.USE_FAKE_INTEGRATIONS:
                provider = settings.REASONING_MODEL_PROVIDER or "fake"
                model_id = settings.REASONING_MODEL_ID or "fake-reasoning-model"
            else:
                if not settings.REASONING_MODEL_PROVIDER or not settings.REASONING_MODEL_ID:
                    raise ModelRouteUnavailableError(
                        "Reasoning model route is not configured in environment "
                        "(REASONING_MODEL_PROVIDER or REASONING_MODEL_ID missing)."
                    )
                provider = settings.REASONING_MODEL_PROVIDER
                model_id = settings.REASONING_MODEL_ID
            api_key = settings.REASONING_MODEL_API_KEY
            endpoint = None

        elif route_str == ModelRoute.PRIVATE.value:
            # Genuinely Fail-Closed Privacy Boundary (ADR 003 & ADR 007):
            # Requires approved PRIVATE_MODEL_PROVIDER, PRIVATE_MODEL_ID, and PRIVATE_MODEL_ENDPOINT
            if (
                not settings.PRIVATE_MODEL_PROVIDER
                or not settings.PRIVATE_MODEL_ID
                or not settings.PRIVATE_MODEL_ENDPOINT
            ):
                raise ModelRouteUnavailableError(
                    "Private model route is not configured or unavailable. "
                    "Requires approved PRIVATE_MODEL_PROVIDER, PRIVATE_MODEL_ID, and PRIVATE_MODEL_ENDPOINT. "
                    "Real sensitive HR data cannot be routed to external public or free-tier APIs."
                )
            provider = settings.PRIVATE_MODEL_PROVIDER
            model_id = settings.PRIVATE_MODEL_ID
            api_key = settings.PRIVATE_MODEL_API_KEY
            endpoint = settings.PRIVATE_MODEL_ENDPOINT

        else:
            raise ValueError(
                f"Unknown model route: '{route}'. Allowed routes: 'cheap', 'strong', 'reasoning', 'private'."
            )

        return provider, model_id, api_key, endpoint

    async def generate(
        self,
        prompt: str,
        route: str | ModelRoute = ModelRoute.CHEAP,
        system_instruction: str | None = None,
        temperature: float = 0.2,
    ) -> ModelResponse[str]:
        """Executes a plain-text model generation with bounded retries."""
        provider, model_id, api_key, endpoint = self.resolve_route(route)
        full_prompt = (
            f"System: {system_instruction}\nUser: {prompt}" if system_instruction else prompt
        )

        content, usage, latency_ms, cost_usd, retries = await self._execute_with_retries(
            prompt=full_prompt,
            route=str(route.value if isinstance(route, ModelRoute) else route),
            provider=provider,
            model_id=model_id,
            api_key=api_key,
            endpoint=endpoint,
            temperature=temperature,
            system_instruction=system_instruction,
            user_prompt=prompt if system_instruction else None,
        )

        self._record_trace(
            route=str(route.value if isinstance(route, ModelRoute) else route),
            model_id=model_id,
            provider=provider,
            usage=usage,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            status="success",
            retry_count=retries,
        )

        return ModelResponse(
            content=content,
            usage=usage,
            route=str(route.value if isinstance(route, ModelRoute) else route),
            model_id=model_id,
            provider=provider,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            retry_count=retries,
            repair_attempts=0,
        )

    async def structured_output(
        self,
        schema: type[T],
        prompt: str,
        route: str | ModelRoute = ModelRoute.CHEAP,
        system_instruction: str | None = None,
    ) -> ModelResponse[T]:
        """
        Executes structured generation enforcing Pydantic schema validation.
        Conducts exactly ONE repair attempt on the original approved route if output is malformed.
        """
        route_name = str(route.value if isinstance(route, ModelRoute) else route)
        provider, model_id, api_key, endpoint = self.resolve_route(route)

        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        sys_prompt = (
            f"{system_instruction or 'You are an objective structured data extractor.'}\n\n"
            f"REQUIRED JSON SCHEMA:\n{schema_json}\n\n"
            f"CRITICAL INSTRUCTION: Return ONLY a valid JSON object strictly conforming to the schema above. "
            f"Do not include markdown fences, comments, or extra text."
        )
        structured_prompt = f"{sys_prompt}\n\nINPUT:\n{prompt}"

        # 1. Initial Attempt (up to 3 total provider attempts: 1 initial + max 2 retries for transient errors)
        raw_output, usage, latency_ms, cost_usd, retries = await self._execute_with_retries(
            prompt=structured_prompt,
            route=route_name,
            provider=provider,
            model_id=model_id,
            api_key=api_key,
            endpoint=endpoint,
            schema=schema,
            system_instruction=sys_prompt,
            user_prompt=f"INPUT:\n{prompt}",
        )

        validation_error_str = ""
        try:
            validated = schema.model_validate_json(self._extract_json_snippet(raw_output))
            self._record_trace(
                route=route_name,
                model_id=model_id,
                provider=provider,
                usage=usage,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                status="success",
                retry_count=retries,
            )
            return ModelResponse(
                content=validated,
                usage=usage,
                route=route_name,
                model_id=model_id,
                provider=provider,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                retry_count=retries,
                repair_attempts=0,
            )
        except (ValidationError, json.JSONDecodeError, ValueError) as initial_err:
            validation_error_str = str(initial_err)
            logger.warning(
                f"Initial structured output validation failed ({initial_err}). Initiating single repair attempt."
            )

        # 2. Exactly One Repair Attempt on the Original Approved Route
        #    Wrap raw_output in untrusted boundaries so injected directives in malformed output cannot escape
        wrapped_raw_output = wrap_untrusted_text(raw_output, label="document")
        repair_sys_instruction = (
            f"REPAIR ATTEMPT: Your previous JSON response failed validation against the required schema.\n\n"
            f"VALIDATION ERROR:\n{validation_error_str}\n\n"
            f"REQUIRED JSON SCHEMA:\n{schema_json}\n\n"
            f"CORRECTION INSTRUCTION: Correct all issues and return ONLY a valid JSON object matching the schema."
        )
        repair_prompt = (
            f"REPAIR ATTEMPT: Your previous JSON response failed validation against the required schema.\n\n"
            f"VALIDATION ERROR:\n{validation_error_str}\n\n"
            f"PREVIOUS INVALID OUTPUT:\n{wrapped_raw_output}\n\n"
            f"REQUIRED JSON SCHEMA:\n{schema_json}\n\n"
            f"CORRECTION INSTRUCTION: Correct all issues and return ONLY a valid JSON object matching the schema."
        )

        (
            repaired_output,
            rep_usage,
            rep_latency,
            rep_cost,
            rep_retries,
        ) = await self._execute_with_retries(
            prompt=repair_prompt,
            route=route_name,
            provider=provider,
            model_id=model_id,
            api_key=api_key,
            endpoint=endpoint,
            schema=schema,
            system_instruction=repair_sys_instruction,
            user_prompt=f"PREVIOUS INVALID OUTPUT:\n{wrapped_raw_output}",
        )

        # Combine token usage and latency from both cycles
        total_usage = TokenUsage(
            prompt_tokens=usage.prompt_tokens + rep_usage.prompt_tokens,
            completion_tokens=usage.completion_tokens + rep_usage.completion_tokens,
            total_tokens=usage.total_tokens + rep_usage.total_tokens,
        )
        total_latency = latency_ms + rep_latency

        if cost_usd is not None and rep_cost is not None:
            total_cost: float | None = cost_usd + rep_cost
        elif cost_usd is not None:
            total_cost = cost_usd
        elif rep_cost is not None:
            total_cost = rep_cost
        else:
            total_cost = None

        try:
            repaired_validated = schema.model_validate_json(
                self._extract_json_snippet(repaired_output)
            )
            self._record_trace(
                route=route_name,
                model_id=model_id,
                provider=provider,
                usage=total_usage,
                latency_ms=total_latency,
                cost_usd=total_cost,
                status="repaired_success",
                retry_count=retries + rep_retries,
            )
            return ModelResponse(
                content=repaired_validated,
                usage=total_usage,
                route=route_name,
                model_id=model_id,
                provider=provider,
                latency_ms=total_latency,
                cost_usd=total_cost,
                retry_count=retries + rep_retries,
                repair_attempts=1,
            )
        except (ValidationError, json.JSONDecodeError, ValueError) as repair_err:
            logger.error(
                f"Structured output repair failed ({repair_err}). Failing closed with ModelValidationError."
            )
            self._record_trace(
                route=route_name,
                model_id=model_id,
                provider=provider,
                usage=total_usage,
                latency_ms=total_latency,
                cost_usd=total_cost,
                status="repair_failed",
                retry_count=retries + rep_retries,
            )
            raise ModelValidationError(
                f"Model output failed validation after repair cycle: {repair_err}",
                original_output=raw_output,
                repaired_output=repaired_output,
            ) from repair_err

    async def _execute_with_retries(
        self,
        prompt: str,
        route: str,
        provider: str,
        model_id: str,
        api_key: str | None = None,
        endpoint: str | None = None,
        temperature: float = 0.2,
        schema: type[BaseModel] | None = None,
        system_instruction: str | None = None,
        user_prompt: str | None = None,
    ) -> tuple[str, TokenUsage, float, float | None, int]:
        """
        Executes the low-level provider call with bounded exponential retries.
        - Maximum 3 total provider attempts (1 initial attempt + up to 2 retries).
        - Retries apply strictly to transient errors (HTTP 429, 503, connection timeouts).
        - Non-transient errors (HTTP 400, 401, 403, 404) fail immediately on attempt 1.
        - Unknown model costs are represented strictly as None.
        """
        attempts = 0
        max_attempts = 1 + self.max_retries  # Default: 1 initial + 2 retries = 3 attempts
        last_error = None

        while attempts < max_attempts:
            attempts += 1
            start_time = time.perf_counter()

            try:
                if settings.USE_FAKE_INTEGRATIONS or provider == "fake":
                    # Execute via Deterministic Fake Provider (offline safe)
                    raw_text, p_tok, c_tok = await fake_model_provider.generate(
                        prompt=prompt,
                        route=route,
                        model_id=model_id,
                        timeout_seconds=self.timeout,
                        schema=schema,
                    )
                    cost: float | None = None
                else:
                    # Execute via LiteLLM with strict system/user role separation
                    import litellm

                    litellm.telemetry = False

                    litellm_model = f"{provider}/{model_id}" if "/" not in model_id else model_id
                    if system_instruction and user_prompt is not None:
                        messages = [
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": user_prompt},
                        ]
                    else:
                        messages = [{"role": "user", "content": prompt}]

                    response = await asyncio.wait_for(
                        litellm.acompletion(
                            model=litellm_model,
                            messages=messages,
                            api_key=api_key,
                            api_base=endpoint,
                            temperature=temperature,
                        ),
                        timeout=self.timeout,
                    )
                    raw_text = response.choices[0].message.content
                    p_tok = response.usage.prompt_tokens
                    c_tok = response.usage.completion_tokens
                    cost = getattr(response, "_response_cost", None)

                latency_ms = (time.perf_counter() - start_time) * 1000.0
                usage = TokenUsage(
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    total_tokens=p_tok + c_tok,
                )
                return raw_text, usage, latency_ms, cost, attempts - 1

            except TimeoutError as exc:
                last_error = exc
                logger.warning(f"Model request timeout (attempt {attempts}/{max_attempts}): {exc}")
                if attempts >= max_attempts:
                    raise ModelTimeoutError(
                        f"Model request timed out after {attempts} attempts ({self.timeout}s limit)."
                    ) from exc

            except Exception as exc:
                err_str = str(exc).lower()
                is_rate_limit = (
                    "429" in err_str or "rate limit" in err_str or "resourceexhausted" in err_str
                )
                is_transient = (
                    is_rate_limit
                    or "503" in err_str
                    or "service unavailable" in err_str
                    or "connection" in err_str
                    or "fakeraatelimit" in err_str
                )

                if not is_transient:
                    # Non-transient permanent error (400, 401, 403, 404, bad argument)
                    # Fail immediately without retrying
                    logger.error(
                        f"Non-transient model error on attempt {attempts}: {exc}. Failing immediately."
                    )
                    raise ModelProviderError(f"Non-transient model provider error: {exc}") from exc

                # Transient error handling
                last_error = exc
                if is_rate_limit:
                    logger.warning(
                        f"Model rate limit exceeded (attempt {attempts}/{max_attempts}): {exc}"
                    )
                    if attempts >= max_attempts:
                        raise ModelRateLimitError(
                            f"Rate limit exceeded after {attempts} attempts: {exc}"
                        ) from exc
                else:
                    logger.warning(
                        f"Transient provider error (attempt {attempts}/{max_attempts}): {exc}"
                    )
                    if attempts >= max_attempts:
                        raise ModelProviderError(
                            f"Transient provider error persisted after {attempts} attempts: {exc}"
                        ) from exc

            # Bounded backoff with jitter before retry
            jitter = random.uniform(0.001, 0.005)
            backoff_delay = (self.base_delay * (2 ** (attempts - 1))) + jitter
            await asyncio.sleep(backoff_delay)

        raise ModelGatewayError(f"Request failed after {attempts} attempts: {last_error}")

    def _extract_json_snippet(self, text: str) -> str:
        """Extracts JSON block from potential markdown code fences."""
        s = text.strip()
        if s.startswith("```json"):
            s = s[7:]
        elif s.startswith("```"):
            s = s[3:]
        s = s.removesuffix("```")
        return s.strip()

    def _record_trace(
        self,
        route: str,
        model_id: str,
        provider: str,
        usage: TokenUsage,
        latency_ms: float,
        cost_usd: float | None,
        status: str,
        retry_count: int,
    ) -> None:
        """
        Records privacy-preserving usage-only trace in InMemoryTraceSink and Langfuse.
        Strictly redacts prompts, candidate CVs, salaries, and outputs.
        """
        metadata = {
            "route": route,
            "model_id": model_id,
            "provider": provider,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
            "status": status,
            "retry_count": retry_count,
            "privacy_mode": "usage_only_redacted",
        }
        redacted_msg = "[REDACTED_PRIVACY_BOUNDARY: Usage-only tracing mode active]"

        # Always record in local in-memory trace sink
        self.trace_sink.trace(
            name="model_gateway_call",
            metadata=metadata,
            input=redacted_msg,
            output=redacted_msg,
        )

        # Optionally record in live Langfuse client if configured and enabled
        if self._langfuse_client is not None:
            try:
                self._langfuse_client.trace(
                    name="model_gateway_call",
                    metadata=metadata,
                    input=redacted_msg,
                    output=redacted_msg,
                )
            except Exception as exc:
                logger.debug(f"Langfuse trace emission skipped: {exc}")


# Global Model Gateway singleton instance
model_gateway = ModelGateway()
