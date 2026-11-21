"""
Dual-Lifecycle State Machine Guards for Policy Documents (Stage 5 Prompt 1).

Strictly separates two orthogonal axes on `policy_documents`:
1. `ingestion_status`: Background parsing/chunking/embedding pipeline state
   (`queued_stub` -> `queued` -> `processing` -> `indexed` | `needs_review` | `failed`)
2. `lifecycle_status`: Human governance & search visibility state
   (`draft` -> `review` -> `active` -> `superseded` | `archived`)

Invariant: Indexing alone (`ingestion_status='indexed'`) can NEVER transition a policy
directly to `lifecycle_status='active'`. Promotion requires `draft -> review -> active`
with an approved `policy.publish` governance approval.
"""

from enum import Enum
from uuid import UUID


class PolicyIngestionStatus(str, Enum):
    """Pipeline states for policy parsing, chunking, and embedding."""

    QUEUED_STUB = "queued_stub"  # Transitional Stage 4 upload stub compatibility (Prompts 1-3)
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    INDEXED = "indexed"
    FAILED = "failed"


class PolicyLifecycleStatus(str, Enum):
    """Governance visibility lifecycle states for corporate policies."""

    DRAFT = "draft"
    REVIEW = "review"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class PolicyDisplayStatus(str, Enum):
    """Unified read-only UI/API display status computed from the two DB status columns."""

    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    REVIEW = "REVIEW"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class PolicyDocType(str, Enum):
    """Authoritative policy corpus categories."""

    COMPANY_POLICY = "company_policy"
    LABOUR_LAW = "labour_law"
    HELPDESK_KNOWLEDGE = "helpdesk_knowledge"


class PolicyVisibility(str, Enum):
    """Access visibility tiers for policy retrieval."""

    ALL = "all"
    MANAGERS = "managers"
    HR_ONLY = "hr_only"


TERMINAL_LIFECYCLE_STATUSES: frozenset[PolicyLifecycleStatus] = frozenset(
    {
        PolicyLifecycleStatus.SUPERSEDED,
        PolicyLifecycleStatus.ARCHIVED,
    }
)

_ALLOWED_INGESTION_TRANSITIONS: dict[PolicyIngestionStatus, frozenset[PolicyIngestionStatus]] = {
    PolicyIngestionStatus.QUEUED_STUB: frozenset({PolicyIngestionStatus.QUEUED}),
    PolicyIngestionStatus.QUEUED: frozenset({PolicyIngestionStatus.PROCESSING}),
    PolicyIngestionStatus.PROCESSING: frozenset(
        {
            PolicyIngestionStatus.INDEXED,
            PolicyIngestionStatus.NEEDS_REVIEW,
            PolicyIngestionStatus.FAILED,
        }
    ),
    PolicyIngestionStatus.FAILED: frozenset({PolicyIngestionStatus.QUEUED}),
    PolicyIngestionStatus.NEEDS_REVIEW: frozenset(
        {
            PolicyIngestionStatus.QUEUED,
            PolicyIngestionStatus.INDEXED,
        }
    ),
    PolicyIngestionStatus.INDEXED: frozenset({PolicyIngestionStatus.QUEUED}),
}

_ALLOWED_LIFECYCLE_TRANSITIONS: dict[PolicyLifecycleStatus, frozenset[PolicyLifecycleStatus]] = {
    PolicyLifecycleStatus.DRAFT: frozenset(
        {
            PolicyLifecycleStatus.REVIEW,
            PolicyLifecycleStatus.ARCHIVED,
        }
    ),
    PolicyLifecycleStatus.REVIEW: frozenset(
        {
            PolicyLifecycleStatus.DRAFT,
            PolicyLifecycleStatus.ACTIVE,
            PolicyLifecycleStatus.ARCHIVED,
        }
    ),
    PolicyLifecycleStatus.ACTIVE: frozenset(
        {
            PolicyLifecycleStatus.SUPERSEDED,
            PolicyLifecycleStatus.ARCHIVED,
        }
    ),
    PolicyLifecycleStatus.SUPERSEDED: frozenset(),
    PolicyLifecycleStatus.ARCHIVED: frozenset(),
}


class PolicyTransitionError(ValueError):
    """Raised when an invalid ingestion or lifecycle state transition is attempted."""

    def __init__(
        self,
        *,
        code: str,
        current_status: str,
        target_status: str,
        reason: str,
    ) -> None:
        self.code = code
        self.current_status = current_status
        self.target_status = target_status
        self.reason = reason
        super().__init__(f"{code}: {current_status} -> {target_status} denied ({reason})")


def _parse_ingestion_status(value: str) -> PolicyIngestionStatus:
    try:
        return PolicyIngestionStatus(value)
    except ValueError as exc:
        raise PolicyTransitionError(
            code="INVALID_INGESTION_STATUS",
            current_status=str(value),
            target_status=str(value),
            reason=f"Unknown ingestion_status '{value}'.",
        ) from exc


def _parse_lifecycle_status(value: str) -> PolicyLifecycleStatus:
    try:
        return PolicyLifecycleStatus(value)
    except ValueError as exc:
        raise PolicyTransitionError(
            code="INVALID_LIFECYCLE_STATUS",
            current_status=str(value),
            target_status=str(value),
            reason=f"Unknown lifecycle_status '{value}'.",
        ) from exc


def assert_ingestion_transition(current: str, target: str) -> str:
    """
    Validates a transition on `policy_documents.ingestion_status`.
    Allows:
      - `queued_stub -> queued`
      - `queued -> processing`
      - `processing -> indexed | needs_review | failed`
      - `failed -> queued` (retry)
      - `needs_review -> queued | indexed` (re-ingest or manual OCR page repair completion)
      - `indexed -> queued` (re-ingest)
    """
    curr_enum = _parse_ingestion_status(current)
    target_enum = _parse_ingestion_status(target)

    if curr_enum == target_enum:
        raise PolicyTransitionError(
            code="NOOP_INGESTION_TRANSITION",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason="Self-transitions are not permitted on ingestion_status.",
        )

    allowed = _ALLOWED_INGESTION_TRANSITIONS.get(curr_enum, frozenset())
    if target_enum not in allowed:
        raise PolicyTransitionError(
            code="INVALID_INGESTION_TRANSITION",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason=(
                f"Cannot transition ingestion_status from '{curr_enum.value}' "
                f"to '{target_enum.value}'."
            ),
        )
    return target_enum.value


def assert_lifecycle_transition(
    current: str,
    target: str,
    *,
    ingestion_status: str,
    approval_id: UUID | str | None = None,
    approval_status: str | None = None,
) -> str:
    """
    Validates a governance transition on `policy_documents.lifecycle_status`.

    Enforces:
      - `superseded` and `archived` are strictly terminal.
      - `draft -> active` is ALWAYS blocked (must pass through `review` + `policy.publish`).
      - `draft -> review` requires `ingestion_status == 'indexed'`.
      - `review -> active` requires `ingestion_status == 'indexed'` AND an approved
        `policy.publish` governance approval (`approval_id` present and `approval_status == 'approved'`).
      - `review -> draft` is allowed when rejected or changes requested.
      - `active -> superseded | archived` and `draft | review -> archived` are allowed.
    """
    curr_enum = _parse_lifecycle_status(current)
    target_enum = _parse_lifecycle_status(target)
    ing_enum = _parse_ingestion_status(ingestion_status)

    if curr_enum in TERMINAL_LIFECYCLE_STATUSES:
        raise PolicyTransitionError(
            code="TERMINAL_LIFECYCLE_STATUS",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason=f"Lifecycle status '{curr_enum.value}' is terminal and cannot transition.",
        )

    if curr_enum == target_enum:
        raise PolicyTransitionError(
            code="NOOP_LIFECYCLE_TRANSITION",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason="Self-transitions are not permitted on lifecycle_status.",
        )

    if curr_enum == PolicyLifecycleStatus.DRAFT and target_enum == PolicyLifecycleStatus.ACTIVE:
        raise PolicyTransitionError(
            code="APPROVAL_WORKFLOW_REQUIRED",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason=(
                "Cannot promote policy directly from 'draft' to 'active'; "
                "must submit to 'review' and obtain an approved policy.publish gate."
            ),
        )

    allowed = _ALLOWED_LIFECYCLE_TRANSITIONS.get(curr_enum, frozenset())
    if target_enum not in allowed:
        raise PolicyTransitionError(
            code="INVALID_LIFECYCLE_TRANSITION",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason=(
                f"Cannot transition lifecycle_status from '{curr_enum.value}' "
                f"to '{target_enum.value}'."
            ),
        )

    if (
        curr_enum == PolicyLifecycleStatus.DRAFT
        and target_enum == PolicyLifecycleStatus.REVIEW
        and ing_enum != PolicyIngestionStatus.INDEXED
    ):
        raise PolicyTransitionError(
            code="DOCUMENT_NOT_INDEXED",
            current_status=curr_enum.value,
            target_status=target_enum.value,
            reason=(
                f"Cannot submit policy for review while ingestion_status is '{ing_enum.value}'; "
                "document must be 'indexed' first."
            ),
        )

    if curr_enum == PolicyLifecycleStatus.REVIEW and target_enum == PolicyLifecycleStatus.ACTIVE:
        if ing_enum != PolicyIngestionStatus.INDEXED:
            raise PolicyTransitionError(
                code="DOCUMENT_NOT_INDEXED",
                current_status=curr_enum.value,
                target_status=target_enum.value,
                reason=(
                    f"Cannot activate policy while ingestion_status is '{ing_enum.value}'; "
                    "document must remain 'indexed'."
                ),
            )
        normalized_approval_status = (approval_status or "").strip().lower()
        if approval_id is None or normalized_approval_status != "approved":
            raise PolicyTransitionError(
                code="APPROVAL_REQUIRED",
                current_status=curr_enum.value,
                target_status=target_enum.value,
                reason=(
                    "Transition 'review -> active' requires an approved policy.publish "
                    "approval_id with approval_status='approved'."
                ),
            )

    return target_enum.value


def compute_display_status(ingestion_status: str, lifecycle_status: str) -> str:
    """
    Computes a single human-readable display badge from the two authoritative DB columns
    (`DRAFT`, `PROCESSING`, `REVIEW`, `ACTIVE`, `SUPERSEDED`, `FAILED`, `ARCHIVED`)
    without ever collapsing the two columns in persistence.
    """
    ing_enum = _parse_ingestion_status(ingestion_status)
    life_enum = _parse_lifecycle_status(lifecycle_status)

    if life_enum == PolicyLifecycleStatus.ARCHIVED:
        return PolicyDisplayStatus.ARCHIVED.value
    if life_enum == PolicyLifecycleStatus.SUPERSEDED:
        return PolicyDisplayStatus.SUPERSEDED.value
    if life_enum == PolicyLifecycleStatus.ACTIVE:
        return PolicyDisplayStatus.ACTIVE.value

    if life_enum == PolicyLifecycleStatus.REVIEW:
        if ing_enum == PolicyIngestionStatus.FAILED:
            return PolicyDisplayStatus.FAILED.value
        if ing_enum in (PolicyIngestionStatus.QUEUED, PolicyIngestionStatus.PROCESSING):
            return PolicyDisplayStatus.PROCESSING.value
        return PolicyDisplayStatus.REVIEW.value

    # life_enum == PolicyLifecycleStatus.DRAFT
    if ing_enum == PolicyIngestionStatus.FAILED:
        return PolicyDisplayStatus.FAILED.value
    if ing_enum == PolicyIngestionStatus.NEEDS_REVIEW:
        return PolicyDisplayStatus.REVIEW.value
    if ing_enum in (PolicyIngestionStatus.QUEUED, PolicyIngestionStatus.PROCESSING):
        return PolicyDisplayStatus.PROCESSING.value
    return PolicyDisplayStatus.DRAFT.value
