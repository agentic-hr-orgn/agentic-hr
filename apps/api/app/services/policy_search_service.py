"""
Hybrid Bilingual Policy Retrieval Engine for Agentic HR Core (Stage 5 Prompt 5, ADR 008).

Executes:
1. Mandatory SQL Security & Lifecycle Pre-Filtering FIRST (RLS + `lifecycle_status='active'`
   + `ingestion_status='indexed'` + role-based visibility on BOTH document and chunk).
2. Identical query normalization (`normalize_for_search`) & `RETRIEVAL_QUERY` embedding.
3. Three pre-filtered retrieval legs:
   - Vector Cosine (`pgvector` HNSW with `SET LOCAL hnsw.ef_search = 100`)
   - PostgreSQL `'simple'` FTS (`plainto_tsquery` + stopword-filtered sanitized OR-fallback)
   - Trigram Lexical (`pg_trgm` `word_similarity` + `similarity`)
4. Weighted Reciprocal Rank Fusion (RRF, k=60: vector=0.55, fts=0.25, trgm=0.20).
5. Deterministic lightweight reranker behind a clean `PolicyReranker` protocol.
6. Focused snippet builder returning canonical Stage 7 `PolicyCitation` metadata.
"""

import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.model_gateway import model_gateway
from app.core.security import AuthenticatedUserContext
from app.models.policy_chunk import PolicyChunk
from app.models.policy_chunk_embedding import PolicyChunkEmbedding
from app.models.policy_document import PolicyDocument
from app.schemas.policy_search import (
    PolicyCitation,
    PolicySearchHit,
    PolicySearchRequest,
    PolicySearchResponse,
)
from app.services.policy_lifecycle import PolicyIngestionStatus, PolicyLifecycleStatus
from app.services.text_normalizer import detect_language, normalize_for_search

RRF_K = 60
RRF_WEIGHT_VECTOR = 0.55
RRF_WEIGHT_FTS = 0.25
RRF_WEIGHT_TRGM = 0.20

_SAFE_TOKEN_RE = re.compile(r"[\u0600-\u06FFa-z0-9]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?۔؟])\s+|\n+")

_RAW_STOPWORDS: tuple[str, ...] = (
    # English stopwords
    "the",
    "is",
    "are",
    "was",
    "were",
    "what",
    "how",
    "many",
    "much",
    "of",
    "in",
    "to",
    "for",
    "a",
    "an",
    "on",
    "by",
    "and",
    "or",
    "do",
    "does",
    "get",
    "per",
    "policy",
    "policies",
    "about",
    "with",
    # Urdu stopwords
    "کیا",
    "ہے",
    "ہیں",
    "کے",
    "کی",
    "کا",
    "کو",
    "میں",
    "سے",
    "پر",
    "اور",
    "کتنے",
    "کتنی",
    "پالیسی",
    "ملتی",
    "ملتا",
    "ہوتا",
    "ہوتی",
)

STOPWORDS: frozenset[str] = frozenset(
    norm_w for w in _RAW_STOPWORDS if (norm_w := normalize_for_search(w))
)


def allowed_visibilities(actor: AuthenticatedUserContext) -> tuple[str, ...]:
    """Computes fail-closed visibility tiers permitted for `actor`."""
    if actor.role == "owner" or bool(actor.is_hr_head):
        return ("all", "managers", "hr_only")
    if actor.role == "manager":
        return ("all", "managers")
    return ("all",)


def extract_query_tokens(normalized_query: str, *, drop_stopwords: bool = True) -> list[str]:
    """Extracts sanitized alphanumeric/Urdu tokens, optionally dropping stopwords with fallback."""
    all_tokens = list(dict.fromkeys(_SAFE_TOKEN_RE.findall(normalized_query or "")))
    if not all_tokens or not drop_stopwords:
        return all_tokens
    filtered = [tok for tok in all_tokens if tok not in STOPWORDS]
    return filtered if filtered else all_tokens


@dataclass
class SearchCandidate:
    """Internal candidate record passed from RRF fusion to `PolicyReranker`."""

    chunk_id: UUID
    document_id: UUID
    title: str
    version: int
    doc_type: str
    visibility: str
    section_path: str
    page_start: int
    page_end: int
    page_range: str
    language: str
    text: str
    normalized_text: str
    has_injection_flag: bool
    vector_rank: int | None = None
    fts_rank: int | None = None
    trgm_rank: int | None = None
    cosine_similarity: float = 0.0
    rrf_score: float = 0.0
    final_score: float = 0.0


class PolicyReranker(Protocol):
    """Pluggable reranker protocol (swappable for a future cross-encoder)."""

    def rerank(
        self,
        *,
        query: str,
        normalized_query: str,
        candidates: list[SearchCandidate],
    ) -> list[SearchCandidate]:
        """Re-scores and sorts fused retrieval candidates."""
        ...


class DeterministicPolicyReranker:
    """
    Lightweight deterministic reranker balancing cross-language semantic recall with lexical precision:
      `score = 0.45 * normalized_rrf + 0.35 * cosine_similarity + 0.12 * term_overlap + 0.08 * heading_match`
    """

    def rerank(
        self,
        *,
        query: str,
        normalized_query: str,
        candidates: list[SearchCandidate],
    ) -> list[SearchCandidate]:
        _ = query
        if not candidates:
            return []

        query_tokens = extract_query_tokens(normalized_query, drop_stopwords=True)
        max_possible_rrf = (RRF_WEIGHT_VECTOR + RRF_WEIGHT_FTS + RRF_WEIGHT_TRGM) / float(RRF_K + 1)

        for cand in candidates:
            norm_rrf = min(1.0, cand.rrf_score / max_possible_rrf) if max_possible_rrf > 0 else 0.0
            cos_sig = max(0.0, min(1.0, cand.cosine_similarity))

            if query_tokens:
                chunk_token_set = set(_SAFE_TOKEN_RE.findall(cand.normalized_text))
                overlap_hits = sum(1 for tok in query_tokens if tok in chunk_token_set)
                term_overlap = overlap_hits / float(len(query_tokens))

                heading_norm = normalize_for_search(f"{cand.title} {cand.section_path}")
                heading_tokens = set(_SAFE_TOKEN_RE.findall(heading_norm))
                heading_hits = sum(1 for tok in query_tokens if tok in heading_tokens)
                heading_match = heading_hits / float(len(query_tokens))
            else:
                term_overlap = 0.0
                heading_match = 0.0

            cand.final_score = round(
                (0.45 * norm_rrf)
                + (0.35 * cos_sig)
                + (0.12 * term_overlap)
                + (0.08 * heading_match),
                6,
            )

        return sorted(
            candidates,
            key=lambda c: (-c.final_score, -c.rrf_score, str(c.chunk_id)),
        )


def build_focused_snippet(text: str, normalized_query: str, max_chars: int = 320) -> str:
    """Extracts a concise readable snippet centered on the best-matching sentence."""
    clean = (text or "").strip()
    if len(clean) <= max_chars:
        return clean

    tokens = extract_query_tokens(normalized_query, drop_stopwords=True)
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(clean) if s.strip()]
    if not sentences:
        return clean[: max_chars - 3].rstrip() + "..."

    best_idx = 0
    best_score = -1
    for idx, sent in enumerate(sentences):
        sent_tokens = set(_SAFE_TOKEN_RE.findall(normalize_for_search(sent)))
        score = sum(1 for tok in tokens if tok in sent_tokens)
        if score > best_score:
            best_score = score
            best_idx = idx

    snippet_parts: list[str] = []
    curr_len = 0
    for sent in sentences[best_idx:]:
        addition = len(sent) + (1 if snippet_parts else 0)
        if snippet_parts and curr_len + addition > max_chars - 3:
            break
        snippet_parts.append(sent)
        curr_len += addition

    joined = " ".join(snippet_parts)
    if len(joined) > max_chars:
        joined = joined[: max_chars - 3].rstrip() + "..."
    elif best_idx > 0:
        joined = "..." + joined
    if len(joined) < len(clean) and not joined.endswith("..."):
        joined = joined.rstrip() + "..."
    return joined


def _base_prefilter_conditions(
    *,
    tenant_uuid: UUID,
    allowed_vis: tuple[str, ...],
    request: PolicySearchRequest,
) -> list[Any]:
    """Builds the mandatory SQL security, lifecycle, and user-filter WHERE clauses."""
    conditions: list[Any] = [
        PolicyChunk.tenant_id == tenant_uuid,
        PolicyDocument.tenant_id == tenant_uuid,
        PolicyDocument.lifecycle_status == PolicyLifecycleStatus.ACTIVE.value,
        PolicyDocument.ingestion_status == PolicyIngestionStatus.INDEXED.value,
        PolicyDocument.visibility.in_(allowed_vis),
        PolicyChunk.visibility.in_(allowed_vis),
    ]
    if request.doc_type is not None:
        conditions.append(PolicyDocument.doc_type == request.doc_type)
    if request.doc_scope is not None:
        conditions.append(PolicyDocument.doc_scope == request.doc_scope)
    if request.language is not None:
        conditions.append(PolicyChunk.language == request.language)
    if request.document_id is not None:
        conditions.append(PolicyDocument.id == request.document_id)
    if request.effective_as_of is not None:
        conditions.append(
            or_(
                PolicyDocument.effective_date.is_(None),
                PolicyDocument.effective_date <= request.effective_as_of,
            )
        )
    return conditions


async def search_policy(
    db: AsyncSession,
    actor: AuthenticatedUserContext,
    request: PolicySearchRequest,
    *,
    reranker: PolicyReranker | None = None,
) -> PolicySearchResponse:
    """
    Executes hybrid bilingual retrieval strictly inside the active tenant and role's
    visibility boundary over `active` + `indexed` policy documents only.
    """
    tenant_uuid = UUID(str(actor.tenant_id))
    allowed_vis = allowed_visibilities(actor)
    normalized_query = normalize_for_search(request.query)
    query_language = detect_language(request.query)

    target_model = model_gateway.resolve_embedding_model()
    target_dim = int(settings.EMBEDDING_DIM or 768)
    candidate_k = max(25, request.top_k * 5)

    # Step 2: Embed normalized query using RETRIEVAL_QUERY task type (L2-normalized)
    emb_input = normalized_query or request.query.strip()
    emb_result = await model_gateway.embed_texts(
        [emb_input],
        task_type="RETRIEVAL_QUERY",
        tenant_id=str(tenant_uuid),
        model=target_model,
        dim=target_dim,
    )
    query_vec = emb_result.embeddings[0]

    base_filters = _base_prefilter_conditions(
        tenant_uuid=tenant_uuid,
        allowed_vis=allowed_vis,
        request=request,
    )

    # Step 3a: Pre-Filtered Vector Cosine Search (HNSW with ef_search=100)
    await db.execute(sa_text("SET LOCAL hnsw.ef_search = 100"))
    dist_col = PolicyChunkEmbedding.embedding.cosine_distance(query_vec).label("cosine_dist")
    vec_stmt = (
        select(PolicyChunk.id, dist_col)
        .join(PolicyDocument, PolicyChunk.document_id == PolicyDocument.id)
        .join(PolicyChunkEmbedding, PolicyChunkEmbedding.chunk_id == PolicyChunk.id)
        .where(
            *base_filters,
            PolicyChunkEmbedding.tenant_id == tenant_uuid,
            PolicyChunkEmbedding.model == target_model,
            PolicyChunkEmbedding.dim == target_dim,
        )
        .order_by(dist_col.asc())
        .limit(candidate_k)
    )
    vec_rows = (await db.execute(vec_stmt)).all()
    vec_ranks: dict[UUID, int] = {}
    vec_sims: dict[UUID, float] = {}
    for rank_idx, (c_id, cos_dist) in enumerate(vec_rows, start=1):
        vec_ranks[c_id] = rank_idx
        vec_sims[c_id] = max(0.0, 1.0 - float(cos_dist))

    # Step 3b & 3c: Lexical FTS ('simple') + Trigram (pg_trgm) — skipped safely if query is empty
    fts_ranks: dict[UUID, int] = {}
    trgm_ranks: dict[UUID, int] = {}

    if normalized_query:
        # 3b. Primary FTS AND match (`plainto_tsquery('simple', ...)`)
        tsq_and = func.plainto_tsquery("simple", normalized_query)
        fts_rank_expr = func.ts_rank_cd(PolicyChunk.tsv, tsq_and).label("fts_score")
        fts_stmt = (
            select(PolicyChunk.id, fts_rank_expr)
            .join(PolicyDocument, PolicyChunk.document_id == PolicyDocument.id)
            .where(*base_filters, PolicyChunk.tsv.op("@@")(tsq_and))
            .order_by(fts_rank_expr.desc())
            .limit(candidate_k)
        )
        fts_rows = (await db.execute(fts_stmt)).all()
        for rank_idx, (c_id, _score) in enumerate(fts_rows, start=1):
            fts_ranks[c_id] = rank_idx

        # Stopword-filtered sanitized OR-fallback when multi-word AND yields < top_k
        or_tokens = extract_query_tokens(normalized_query, drop_stopwords=True)
        if len(fts_ranks) < request.top_k and len(or_tokens) >= 2:
            or_query_str = " | ".join(or_tokens[:16])
            tsq_or = func.to_tsquery("simple", or_query_str)
            or_rank_expr = func.ts_rank_cd(PolicyChunk.tsv, tsq_or).label("fts_or_score")
            or_stmt = (
                select(PolicyChunk.id, or_rank_expr)
                .join(PolicyDocument, PolicyChunk.document_id == PolicyDocument.id)
                .where(*base_filters, PolicyChunk.tsv.op("@@")(tsq_or))
                .order_by(or_rank_expr.desc())
                .limit(candidate_k)
            )
            or_rows = (await db.execute(or_stmt)).all()
            next_rank = len(fts_ranks) + 1
            for c_id, _score in or_rows:
                if c_id not in fts_ranks:
                    fts_ranks[c_id] = next_rank
                    next_rank += 1
                    if len(fts_ranks) >= candidate_k:
                        break

        # 3c. Trigram lexical leg (pg_trgm word_similarity + similarity)
        sim_expr = func.similarity(PolicyChunk.normalized_text, normalized_query)
        wsim_expr = func.word_similarity(normalized_query, PolicyChunk.normalized_text)
        trgm_score_expr = (0.4 * sim_expr + 0.6 * wsim_expr).label("trgm_score")
        trgm_stmt = (
            select(PolicyChunk.id, trgm_score_expr)
            .join(PolicyDocument, PolicyChunk.document_id == PolicyDocument.id)
            .where(*base_filters, or_(wsim_expr >= 0.12, sim_expr >= 0.05))
            .order_by(trgm_score_expr.desc())
            .limit(candidate_k)
        )
        trgm_rows = (await db.execute(trgm_stmt)).all()
        for rank_idx, (c_id, _score) in enumerate(trgm_rows, start=1):
            trgm_ranks[c_id] = rank_idx

    # Step 4: Weighted Reciprocal Rank Fusion (RRF, k=60)
    all_chunk_ids = set(vec_ranks.keys()) | set(fts_ranks.keys()) | set(trgm_ranks.keys())
    if not all_chunk_ids:
        return PolicySearchResponse(
            query=request.query,
            normalized_query=normalized_query,
            query_language=query_language,
            total_candidates=0,
            hits=[],
            embedding_model=target_model,
            embedding_dim=target_dim,
        )

    rrf_scores: dict[UUID, float] = {}
    for c_id in all_chunk_ids:
        score = 0.0
        if c_id in vec_ranks:
            score += RRF_WEIGHT_VECTOR / float(RRF_K + vec_ranks[c_id])
        if c_id in fts_ranks:
            score += RRF_WEIGHT_FTS / float(RRF_K + fts_ranks[c_id])
        if c_id in trgm_ranks:
            score += RRF_WEIGHT_TRGM / float(RRF_K + trgm_ranks[c_id])
        rrf_scores[c_id] = round(score, 6)

    rerank_pool_size = max(request.top_k * 3, 15)
    top_fused_ids = sorted(
        all_chunk_ids,
        key=lambda cid: (-rrf_scores[cid], str(cid)),
    )[:rerank_pool_size]

    # Hydrate top fused candidates cleanly (re-enforcing base pre-filters)
    hydrate_stmt = (
        select(PolicyChunk, PolicyDocument)
        .join(PolicyDocument, PolicyChunk.document_id == PolicyDocument.id)
        .where(*base_filters, PolicyChunk.id.in_(top_fused_ids))
    )
    hydrated_rows = (await db.execute(hydrate_stmt)).all()

    candidates: list[SearchCandidate] = []
    for chunk, doc in hydrated_rows:
        candidates.append(
            SearchCandidate(
                chunk_id=chunk.id,
                document_id=doc.id,
                title=doc.title,
                version=doc.version,
                doc_type=doc.doc_type,
                visibility=doc.visibility,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                page_range=chunk.page_range,
                language=chunk.language,
                text=chunk.text,
                normalized_text=chunk.normalized_text,
                has_injection_flag=bool(chunk.has_injection_flag),
                vector_rank=vec_ranks.get(chunk.id),
                fts_rank=fts_ranks.get(chunk.id),
                trgm_rank=trgm_ranks.get(chunk.id),
                cosine_similarity=vec_sims.get(chunk.id, 0.0),
                rrf_score=rrf_scores.get(chunk.id, 0.0),
            )
        )

    # Step 5: Lightweight deterministic reranker
    active_reranker = reranker or DeterministicPolicyReranker()
    reranked = active_reranker.rerank(
        query=request.query,
        normalized_query=normalized_query,
        candidates=candidates,
    )[: request.top_k]

    # Step 6: Build Stage 7 PolicyCitation + focused snippets
    hits = [
        PolicySearchHit(
            citation=PolicyCitation(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                title=c.title,
                version=c.version,
                doc_type=c.doc_type,
                visibility=c.visibility,
                section_path=c.section_path,
                page_start=c.page_start,
                page_end=c.page_end,
                page_range=c.page_range,
                language=c.language,
            ),
            text=c.text,
            snippet=build_focused_snippet(c.text, normalized_query),
            score=c.final_score,
            rrf_score=c.rrf_score,
            vector_rank=c.vector_rank,
            fts_rank=c.fts_rank,
            trgm_rank=c.trgm_rank,
            has_injection_flag=c.has_injection_flag,
        )
        for c in reranked
    ]

    return PolicySearchResponse(
        query=request.query,
        normalized_query=normalized_query,
        query_language=query_language,
        total_candidates=len(all_chunk_ids),
        hits=hits,
        embedding_model=target_model,
        embedding_dim=target_dim,
    )
