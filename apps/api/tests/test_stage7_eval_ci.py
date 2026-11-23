"""
Stage 7 Prompt 4: CI Evaluation Suite (`fake` mode) against real PostgreSQL Forced RLS.

Verifies that all 18 benchmark questions achieve 100% pass accuracy and 100% citation
verification pass rate in deterministic CI mode (`USE_FAKE_INTEGRATIONS=true`).
"""

from __future__ import annotations

import pytest

from app.evals.run_stage7_eval import run_stage7_eval


@pytest.mark.asyncio
async def test_stage7_eval_18_fake_mode_passes_100_percent() -> None:
    report = await run_stage7_eval(mode="fake", output_path=None)
    summary = report["summary"]

    failed_rows = [row for row in report["results"] if not row["passed"]]
    assert summary["total"] == 18, f"Expected 18 eval questions, got {summary['total']}"
    assert summary["passed"] == 18, (
        f"Expected 18/18 passing questions in fake mode, failures={failed_rows}"
    )
    assert summary["accuracy_pct"] == 100.0
    assert summary["citation_verification_pct"] == 100.0
