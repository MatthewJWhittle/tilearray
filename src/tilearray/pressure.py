"""Shared HTTP response classification for retry and AIMD pressure."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

_BODY_SAMPLE_BYTES = 4096


@dataclass(frozen=True)
class PressureSignature:
    """Pluggable match rule: HTTP status plus optional body substrings.

    ``max_retries`` caps how many times this signature may retry within one
    tile fetch (``None`` uses the full ``FetchPolicy.retries`` budget). Host
    presets that see rare transient 404s can register a bounded signature
    without burning the whole retry budget on a permanent miss.
    """

    name: str
    status_codes: frozenset[int]
    body_substrings: tuple[bytes, ...] = ()
    retryable: bool = True
    aimd_pressure: bool = True
    circuit_breaker: bool = False
    max_retries: int | None = None


@dataclass(frozen=True)
class ResponseClassification:
    """Outcome of classifying one HTTP response."""

    signature: PressureSignature | None = None
    retryable: bool = False
    aimd_pressure: bool = False
    circuit_breaker: bool = False
    max_retries: int | None = None


def _gateway_status_codes() -> frozenset[int]:
    return frozenset({403, 408, 429, 500, 502, 503, 504})


BUILTIN_PRESSURE_SIGNATURES: tuple[PressureSignature, ...] = (
    PressureSignature(
        name="gateway_forbidden",
        status_codes=frozenset({403}),
        circuit_breaker=True,
    ),
    PressureSignature(
        name="gateway_timeout",
        status_codes=frozenset({408}),
    ),
    PressureSignature(
        name="rate_limit",
        status_codes=frozenset({429}),
        circuit_breaker=True,
    ),
    PressureSignature(
        name="upstream_error",
        status_codes=frozenset({500, 502, 503, 504}),
    ),
)
# HTTP 404 (including OGC ExceptionReport bodies such as InvalidSubsetting /
# InvalidParameterValue / NoSuchCoverage) is a permanent client error — no
# built-in retryable 404 signature. Hosts that truly emit transient 404s can
# register an explicit host-specific PressureSignature.


@dataclass
class PressureClassifier:
    """Match responses against ordered signatures (first match wins)."""

    signatures: tuple[PressureSignature, ...] = field(
        default_factory=lambda: BUILTIN_PRESSURE_SIGNATURES
    )

    def register(self, signature: PressureSignature) -> None:
        """Append a custom signature (checked after existing rules)."""

        self.signatures = (*self.signatures, signature)

    def classify(self, response: httpx.Response) -> ResponseClassification:
        body = response.content[:_BODY_SAMPLE_BYTES]
        lowered_body = body.lower() if body else b""

        for signature in self.signatures:
            if response.status_code not in signature.status_codes:
                continue
            if signature.body_substrings and not any(
                needle in lowered_body for needle in signature.body_substrings
            ):
                continue
            return ResponseClassification(
                signature=signature,
                retryable=signature.retryable,
                aimd_pressure=signature.aimd_pressure,
                circuit_breaker=signature.circuit_breaker,
                max_retries=signature.max_retries,
            )

        return ResponseClassification()


DEFAULT_PRESSURE_CLASSIFIER = PressureClassifier()


def classify_response(
    response: httpx.Response,
    classifier: PressureClassifier | None = None,
) -> ResponseClassification:
    """Classify ``response`` for retry / AIMD / circuit-breaker handling."""

    return (classifier or DEFAULT_PRESSURE_CLASSIFIER).classify(response)


def register_pressure_signature(signature: PressureSignature) -> None:
    """Register a signature on the process-wide default classifier."""

    DEFAULT_PRESSURE_CLASSIFIER.register(signature)


def reset_pressure_classifier(
    signatures: Sequence[PressureSignature] | None = None,
) -> None:
    """Reset the default classifier (primarily for tests)."""

    DEFAULT_PRESSURE_CLASSIFIER.signatures = tuple(
        signatures if signatures is not None else BUILTIN_PRESSURE_SIGNATURES
    )
