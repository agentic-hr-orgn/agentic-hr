"""
Reusable PostgreSQL Custom SQLAlchemy Types for Agentic HR Core.
Provides a zero-dependency PGVector(dim) UserDefinedType compatible with asyncpg
and pgvector cosine distance queries (`<=>`).
"""

import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Float
from sqlalchemy.types import UserDefinedType


def format_pgvector(values: Sequence[float] | str, expected_dim: int = 768) -> str:
    """Validates vector dimensionality/finiteness and formats as pgvector text literal."""
    if isinstance(values, str):
        parsed = parse_pgvector(values, expected_dim=expected_dim)
        assert parsed is not None
        vals = parsed
    else:
        vals = [float(v) for v in values]

    if len(vals) != expected_dim:
        raise ValueError(f"Expected {expected_dim}-dimensional vector, got {len(vals)}.")

    for idx, val in enumerate(vals):
        if not math.isfinite(val):
            raise ValueError(f"Vector element at index {idx} is not finite: {val!r}")

    return "[" + ",".join(f"{v:.8g}" for v in vals) + "]"


def parse_pgvector(
    raw: str | Sequence[float] | None,
    expected_dim: int | None = None,
) -> list[float] | None:
    """Parses a pgvector text literal (`[0.1,0.2,...]`) or sequence into list[float]."""
    if raw is None:
        return None

    if isinstance(raw, str):
        stripped = raw.strip()
        if not (stripped.startswith("[") and stripped.endswith("]")):
            raise ValueError(f"Invalid pgvector literal format: {raw!r}")
        inner = stripped[1:-1].strip()
        vals = [] if not inner else [float(part.strip()) for part in inner.split(",")]
    else:
        vals = [float(v) for v in raw]

    if expected_dim is not None and len(vals) != expected_dim:
        raise ValueError(f"Expected {expected_dim}-dimensional vector, got {len(vals)}.")

    for idx, val in enumerate(vals):
        if not math.isfinite(val):
            raise ValueError(f"Vector element at index {idx} is not finite: {val!r}")

    return vals


class PGVector(UserDefinedType):
    """SQLAlchemy type mapping PostgreSQL `vector(dim)` columns for asyncpg."""

    cache_ok = True

    class Comparator(UserDefinedType.Comparator[list[float]]):
        """Column comparator exposing pgvector distance operators."""

        def cosine_distance(self, other: Any) -> Any:
            """Computes pgvector cosine distance (`embedding <=> other`) as Float."""
            return self.op("<=>", return_type=Float)(other)

    comparator_factory = Comparator

    def __init__(self, dim: int = 768) -> None:
        if dim <= 0:
            raise ValueError(f"PGVector dim must be positive, got {dim}.")
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        _ = kw
        return f"vector({self.dim})"

    def bind_processor(self, dialect: Any):
        _ = dialect
        dim = self.dim

        def process(value: Sequence[float] | str | None) -> str | None:
            if value is None:
                return None
            return format_pgvector(value, expected_dim=dim)

        return process

    def result_processor(self, dialect: Any, coltype: Any):
        _ = dialect, coltype
        dim = self.dim

        def process(value: str | Sequence[float] | None) -> list[float] | None:
            if value is None:
                return None
            return parse_pgvector(value, expected_dim=dim)

        return process
