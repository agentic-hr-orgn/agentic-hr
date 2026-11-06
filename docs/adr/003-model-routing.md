# ADR 003: Model Routing & Free-Tier Privacy Boundary
Status: Decided (Updated with Gemini 3 Series Free-Tier Models)
Context: Require 100% free-tier AI API routing for development and testing while strictly enforcing no-training privacy policies.
Decision:
1. Strong Model: `gemini-3.8-flash` (Google AI Studio Free Tier, 15 RPM, 1,500 RPD, 1M context) for Policy Brain & complex reasoning.
2. Cheap Model: `gemini-3.5-flash-lite` (Google AI Studio Free Tier, 30 RPM, 1,500 RPD, 1M context) for bulk document parsing & candidate screening.
3. Multilingual Embedding: `gemini-embedding-2` (Google AI Studio Free Tier, 100 RPM, 1,500 RPD, MRL dimensions: 768/1536/3072) for Urdu+English bilingual policy RAG.
4. Privacy Boundary: Free Google API tier content may be used to train/improve Google products. Therefore, ONLY synthetic data (Acme Tech Solutions) is permitted on free-tier APIs. Real employee or candidate data must never be sent through free-tier APIs under our strict no-training requirement.
5. Stage 1 Execution: Keep USE_FAKE_INTEGRATIONS=true throughout Stage 1; no live AI API calls during platform skeleton.
Consequences: Zero-cost AI development and testing; clear upgrade path to Vertex AI / paid Google Cloud project keys with no-training guarantees for pilot and production.
