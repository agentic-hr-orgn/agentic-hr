# ADR 008: Stage 5 Policy Embeddings, Parsing & Retrieval-Only Scope

Status: Decided (Amends ADR 003 Embedding Model; Aligns with ADR 007 Privacy Governance)
Date: 2026-11-21

## Context
Stage 5 introduces **Policy Ingestion & Bilingual Search** (the RAG storage, parsing, chunking, embedding, and hybrid retrieval foundation for the Policy Brain). To support English, Urdu (Arabic script), and Roman Urdu corporate policies while preserving strict multi-tenant Row-Level Security (RLS), zero-AGPL licensing hygiene, and data sovereignty guarantees from ADR 003 and ADR 007, we need explicit decisions on:
1. Embedding model, dimensionality, normalization, storage layout, and privacy routing.
2. Document parsing and OCR libraries for PDF, DOCX, and TXT uploads.
3. Retrieval-only API boundary prior to Stage 7 LLM answer generation and evaluation.

---

## Decision

### 1. Bilingual Embeddings & Storage Architecture
- **Primary Embedding Model (`gemini-embedding-001`)**:
  - Supersedes ADR 003's initial placeholder reference (`gemini-embedding-2`) with `gemini-embedding-001` configured at **768 dimensions** via Matryoshka Representation Learning (`output_dimensionality=768`).
  - **Mandatory L2 Normalization**: Because Matryoshka truncation below the native 3072 dimensions is not pre-normalized by the API, every embedding vector **MUST** be L2-normalized (`v / ||v||_2`, rejecting zero-norm or non-finite vectors) before persistence or cosine distance comparison.
- **Asymmetric Task Types**:
  - Ingestion/indexing uses `task_type="RETRIEVAL_DOCUMENT"`.
  - Search queries use `task_type="RETRIEVAL_QUERY"`.
- **Separate Embedding Table (`public.policy_chunk_embeddings`)**:
  - Embeddings are stored in a dedicated forced-RLS table `public.policy_chunk_embeddings` keyed by `(chunk_id, model, dim)` rather than a column on `public.policy_chunks`.
  - Includes an idempotent reuse lookup index on `(tenant_id, model, dim, content_hash)` so re-ingesting unchanged chunks or upgrading embedding models requires zero rewrites to `policy_chunks` and zero duplicate API calls.
  - Indexed via pgvector HNSW cosine index: `USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`.
- **Deterministic Offline Fake Embedder**:
  - Under `USE_FAKE_INTEGRATIONS=true` (CI and unit/integration suites), a deterministic, offline-safe multilingual fake embedder generates normalized 768-dim vectors supporting English, Urdu/Arabic script, and Roman Urdu lexical/n-gram features so hybrid search tests run reliably with zero network calls.
- **Privacy & Model Routing Governance (ADR 003 & ADR 007)**:
  - Free-tier Google AI Studio embedding keys are permitted **exclusively for synthetic Acme Tech Solutions test data**.
  - Customer/pilot policy corpora **MUST** use a **paid Gemini API / Vertex AI tier** with contractual zero-data-retention and zero-training guarantees.
  - **`bge-m3`** is reserved as the future `ModelRoute.PRIVATE` (`"private"`) self-hosted VPC embedding route for strict on-premise or high-sensitivity deployments.

---

### 2. Permissive-Licensed Document Parsing & Optional OCR
- **Layered Extraction Stack (Zero AGPL)**:
  - **PDF**: `pdfplumber` (MIT) as primary layout/table-aware extractor, paired with `pypdfium2` (Apache-2.0) for page rendering and low-level fallback.
  - **DOCX**: `python-docx` (MIT) preserving heading hierarchy and section paths.
  - **TXT**: Strict UTF-8 decoding (handling UTF-8 BOM and Unicode NFC normalization) with structural heading detection.
  - **Explicitly Prohibited**: `PyMuPDF` (`fitz`) is strictly excluded due to its AGPL-3.0 license.
  - **Deferred**: `Docling` (heavy ML layout/OCR dependency) is deferred to the dedicated Stage 14 worker container so the Stage 5 FastAPI and Temporal worker footprint remains fast and lightweight.
- **Optional Tesseract OCR Fallback (`urd+eng`)**:
  - Pages with low extracted text density or corrupted glyph streams are flagged per page in `policy_document_pages` (`needs_ocr=true`, `extraction_status='needs_ocr'`).
  - When Tesseract (`urd+eng`) is available in the runtime environment, OCR is applied (`ocr_applied=true`).
  - When OCR is unavailable or confidence remains below threshold, the document deterministically transitions to `ingestion_status='needs_review'` and records offending page numbers in `low_confidence_pages`, preventing silent empty-index success.

---

### 3. Retrieval-Only Scope in Stage 5 & Dual-Lifecycle Governance
- **No `/ask` LLM Generation in Stage 5**:
  - Stage 5 delivers document ingestion, chunking, embedding, hybrid search, and approval-gated publishing only. End-user `/ask` synthesis, grounded inline citations, refusal thresholds, and RAG evaluation harnesses are deferred to Stage 7.
- **Hybrid Bilingual Retrieval Stack**:
  - Combines three complementary signals inside PostgreSQL under forced RLS:
    1. **Keyword Full-Text Search**: `tsvector` using `'simple'` text search configuration (`GIN (tsv)`) so English, Roman Urdu, and Urdu tokens are preserved without English-only stemmer distortion.
    2. **Fuzzy / Trigram Similarity**: `pg_trgm` (`GIN (normalized_text gin_trgm_ops)`) to handle typos, transliterated Roman Urdu spelling variations, and partial terms.
    3. **Semantic Vector Cosine Search**: `pgvector` HNSW cosine distance (`embedding <=> query_vector`).
  - Candidate lists are fused deterministically via **Reciprocal Rank Fusion (RRF)**.
- **Dual-Lifecycle Isolation**:
  - `policy_documents` separates pipeline state (`ingestion_status`: `queued | processing | needs_review | indexed | failed`) from governance visibility (`lifecycle_status`: `draft | review | active | superseded | archived`).
  - Indexing alone (`ingestion_status='indexed'`) never promotes a policy to `active`; promotion requires `draft -> review -> active` gated by an approved `policy.publish` workflow.

---

## Consequences
- Clean, permissive dependency tree (`MIT` / `Apache-2.0`) with zero AGPL contamination.
- Zero-downtime embedding model upgrades (`gemini-embedding-001` -> future `bge-m3` private route) without altering `policy_chunks`.
- Deterministic offline CI testing with full English, Urdu script, and Roman Urdu coverage.
