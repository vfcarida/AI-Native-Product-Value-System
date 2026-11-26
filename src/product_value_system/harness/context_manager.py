"""
4-Layer Context Engineering & Memory Architecture with OWASP ASI06
Memory-Poisoning Protection (EVO-P3-12).

Layers:
1. Working Memory: Dynamic token-budgeted conversation turns with automatic
   history compaction while pinning invariant system instructions.
2. Episodic Memory: Cross-run structured summaries scoped by ``tenant_id``.
3. Semantic Knowledge Store: Hybrid BM25 lexical + dense vector cosine
   retrieval combined via Reciprocal Rank Fusion (RRF).
4. Structured Artifacts: Versioned typed artifact store.

Security (OWASP ASI06):
- Every write is scanned by ``InputValidator`` to block prompt-injection
  poisoning before entering long-term memory.
- Every persisted record stores a canonical SHA-256 integrity digest verified
  on every read (raising ``MemoryIntegrityError`` on tampering).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from product_value_system.adapters.embedding_adapter import (
    EmbeddingAdapter,
    LocalHashEmbeddingAdapter,
)
from product_value_system.adapters.model_adapter import Message, Role
from product_value_system.caching.similarity_matcher import cosine_similarity
from product_value_system.security.input_validator import InputValidator, SecurityError

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


class MemoryIntegrityError(RuntimeError):
    """Raised when a stored episodic, semantic, or artifact record fails SHA-256 verification."""


def estimate_tokens(text: str) -> int:
    """Fast deterministic token estimator (~4 chars per token)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def compute_record_checksum(fields: dict[str, Any]) -> str:
    """Compute canonical SHA-256 checksum for OWASP ASI06 tamper detection."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class EpisodicEntry:
    """Cross-run episodic summary record with SHA-256 integrity digest."""

    entry_id: str
    tenant_id: str
    run_id: str
    stage: str
    summary: str
    outcome: str
    created_at: datetime
    checksum_sha256: str = ""

    def compute_checksum(self) -> str:
        return compute_record_checksum(
            {
                "entry_id": self.entry_id,
                "tenant_id": self.tenant_id,
                "run_id": self.run_id,
                "stage": self.stage,
                "summary": self.summary,
                "outcome": self.outcome,
            }
        )

    def verify_integrity(self) -> None:
        if self.checksum_sha256 != self.compute_checksum():
            raise MemoryIntegrityError(
                f"Episodic entry '{self.entry_id}' failed OWASP ASI06 integrity verification"
            )


@dataclass
class SemanticChunk:
    """Tenant-scoped semantic knowledge chunk with embedding and SHA-256 checksum."""

    chunk_id: str
    tenant_id: str
    source_id: str
    content: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    checksum_sha256: str = ""

    def compute_checksum(self) -> str:
        return compute_record_checksum(
            {
                "chunk_id": self.chunk_id,
                "tenant_id": self.tenant_id,
                "source_id": self.source_id,
                "content": self.content,
                "metadata": self.metadata,
            }
        )

    def verify_integrity(self) -> None:
        if self.checksum_sha256 != self.compute_checksum():
            raise MemoryIntegrityError(
                f"Semantic chunk '{self.chunk_id}' failed OWASP ASI06 integrity verification"
            )


@dataclass
class ArtifactEntry:
    """Versioned structured artifact with SHA-256 integrity checksum."""

    key: str
    tenant_id: str
    version: int
    payload: Any
    updated_at: datetime
    checksum_sha256: str = ""

    def compute_checksum(self) -> str:
        return compute_record_checksum(
            {
                "key": self.key,
                "tenant_id": self.tenant_id,
                "version": self.version,
                "payload": self.payload,
            }
        )

    def verify_integrity(self) -> None:
        if self.checksum_sha256 != self.compute_checksum():
            raise MemoryIntegrityError(
                f"Artifact '{self.key}' v{self.version} failed OWASP ASI06 integrity verification"
            )


@dataclass(frozen=True)
class AssembledContext:
    """Token-budgeted context package ready for ``Harness.invoke_model()``."""

    messages: list[Message]
    used_tokens: int
    token_budget: int
    compacted_turns: int
    retrieved_chunks: list[SemanticChunk]
    episodic_hits: list[EpisodicEntry]


def _bm25_scores(
    query: str,
    documents: list[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Pure-Python BM25 lexical relevance scores over a candidate document list."""
    if not documents:
        return []
    query_terms = _TOKEN_RE.findall(query.lower())
    if not query_terms:
        return [0.0] * len(documents)

    tokenized_docs = [_TOKEN_RE.findall(doc.lower()) for doc in documents]
    n_docs = len(tokenized_docs)
    avgdl = sum(len(d) for d in tokenized_docs) / max(1, n_docs)

    df: Counter[str] = Counter()
    for doc_tokens in tokenized_docs:
        for term in set(doc_tokens):
            df[term] += 1

    scores: list[float] = []
    for doc_tokens in tokenized_docs:
        tf = Counter(doc_tokens)
        dl = len(doc_tokens)
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            freq = tf[term]
            denom = freq + k1 * (1.0 - b + b * (dl / max(1.0, avgdl)))
            score += idf * ((freq * (k1 + 1.0)) / max(1e-9, denom))
        scores.append(score)
    return scores


class ContextManager:
    """
    4-layer Context Engineering manager (Working, Episodic, Semantic, Artifacts)
    with dynamic token budgeting, hybrid BM25+vector retrieval, and OWASP ASI06 guards.
    """

    def __init__(
        self,
        *,
        max_working_tokens: int = 4096,
        embedding_adapter: EmbeddingAdapter | None = None,
        input_validator: InputValidator | None = None,
        enforce_integrity: bool = True,
    ) -> None:
        self.max_working_tokens = max(128, int(max_working_tokens))
        self.embedding_adapter: EmbeddingAdapter = (
            embedding_adapter or LocalHashEmbeddingAdapter()
        )
        self.input_validator = input_validator or InputValidator()
        self.enforce_integrity = enforce_integrity

        # Layer 1: Working Memory
        self._working_messages: list[Message] = []
        self._compacted_summaries: list[str] = []
        self._compacted_turn_count: int = 0

        # Layer 2: Episodic Memory
        self._episodic: list[EpisodicEntry] = []

        # Layer 3: Semantic Knowledge Store
        self._semantic_chunks: list[SemanticChunk] = []

        # Layer 4: Structured Artifacts (tenant -> key -> ArtifactEntry)
        self._artifacts: dict[str, dict[str, ArtifactEntry]] = {}

    def _guard_text(self, text: str, *, layer: str) -> None:
        """Scan untrusted content before storing to prevent OWASP ASI06 memory poisoning."""
        if not self.enforce_integrity:
            return
        result = self.input_validator.validate(text)
        if not result.is_valid:
            raise SecurityError(
                f"Memory write blocked on {layer} layer (OWASP ASI06): {result.reason}",
                risk_level=result.risk_level,
                matched_patterns=result.matched_patterns,
            )

    # ------------------------------------------------------------------
    # Layer 1: Working Memory + Dynamic Token Compaction
    # ------------------------------------------------------------------
    def add_working_message(
        self,
        role: Role | str,
        content: str,
        *,
        cache_control: bool = False,
    ) -> None:
        resolved_role = role if isinstance(role, Role) else Role(str(role))
        if resolved_role in {Role.USER, Role.TOOL}:
            self._guard_text(content, layer="working")
        self._working_messages.append(
            Message(role=resolved_role, content=content, cache_control=cache_control)
        )
        self._compact_if_needed()

    def _compact_if_needed(self) -> None:
        """Compact oldest non-system working turns into a concise summary if over budget."""
        system_msgs = [m for m in self._working_messages if m.role == Role.SYSTEM]
        non_system = [m for m in self._working_messages if m.role != Role.SYSTEM]

        def _total_tokens() -> int:
            return sum(estimate_tokens(m.content) for m in system_msgs) + sum(
                estimate_tokens(m.content) for m in non_system
            ) + sum(estimate_tokens(s) for s in self._compacted_summaries)

        while len(non_system) > 2 and _total_tokens() > self.max_working_tokens:
            dropped = non_system.pop(0)
            snippet = " ".join(dropped.content.split())[:140]
            self._compacted_summaries.append(f"[{dropped.role.value}] {snippet}")
            self._compacted_turn_count += 1

        self._working_messages = system_msgs + non_system

    # ------------------------------------------------------------------
    # Layer 2: Episodic Memory
    # ------------------------------------------------------------------
    def record_episode(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stage: str,
        summary: str,
        outcome: str = "completed",
    ) -> EpisodicEntry:
        self._guard_text(summary, layer="episodic")
        entry = EpisodicEntry(
            entry_id=f"ep-{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            run_id=run_id,
            stage=stage,
            summary=summary,
            outcome=outcome,
            created_at=datetime.now(UTC),
        )
        entry.checksum_sha256 = entry.compute_checksum()
        self._episodic.append(entry)
        return entry

    def get_episodes(
        self,
        *,
        tenant_id: str,
        limit: int = 5,
    ) -> list[EpisodicEntry]:
        matching = [e for e in self._episodic if e.tenant_id == tenant_id]
        recent = matching[-max(1, limit) :]
        for item in recent:
            if self.enforce_integrity:
                item.verify_integrity()
        return recent

    # ------------------------------------------------------------------
    # Layer 3: Semantic Knowledge Store (Hybrid BM25 + Dense Vector RRF)
    # ------------------------------------------------------------------
    async def index_semantic_chunk(
        self,
        *,
        tenant_id: str,
        source_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> SemanticChunk:
        self._guard_text(content, layer="semantic")
        embedding = await self.embedding_adapter.embed(content)
        chunk = SemanticChunk(
            chunk_id=f"chk-{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            source_id=source_id,
            content=content,
            embedding=embedding,
            metadata=dict(metadata or {}),
        )
        chunk.checksum_sha256 = chunk.compute_checksum()
        self._semantic_chunks.append(chunk)
        return chunk

    async def search_semantic(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int = 4,
        rrf_k: int = 60,
    ) -> list[tuple[SemanticChunk, float]]:
        """Hybrid BM25 + vector cosine retrieval combined via Reciprocal Rank Fusion."""
        candidates = [c for c in self._semantic_chunks if c.tenant_id == tenant_id]
        if not candidates:
            return []
        if self.enforce_integrity:
            for chunk in candidates:
                chunk.verify_integrity()

        query_vec = await self.embedding_adapter.embed(query)
        vec_scores = [cosine_similarity(query_vec, c.embedding) for c in candidates]
        lex_scores = _bm25_scores(query, [c.content for c in candidates])

        vec_order = sorted(range(len(candidates)), key=lambda i: vec_scores[i], reverse=True)
        lex_order = sorted(range(len(candidates)), key=lambda i: lex_scores[i], reverse=True)

        vec_ranks = {idx: rank + 1 for rank, idx in enumerate(vec_order)}
        lex_ranks = {idx: rank + 1 for rank, idx in enumerate(lex_order)}

        fused: list[tuple[SemanticChunk, float]] = []
        for idx, chunk in enumerate(candidates):
            rrf_score = (1.0 / (rrf_k + vec_ranks[idx])) + (1.0 / (rrf_k + lex_ranks[idx]))
            # Tie-break gently with direct cosine similarity
            fused.append((chunk, round(rrf_score + 0.01 * max(0.0, vec_scores[idx]), 6)))

        fused.sort(key=lambda pair: pair[1], reverse=True)
        return fused[: max(1, top_k)]

    # ------------------------------------------------------------------
    # Layer 4: Structured Artifacts
    # ------------------------------------------------------------------
    def put_artifact(self, *, tenant_id: str, key: str, payload: Any) -> ArtifactEntry:
        serialized = json.dumps(payload, default=str)
        self._guard_text(serialized, layer="artifact")
        tenant_bucket = self._artifacts.setdefault(tenant_id, {})
        prev = tenant_bucket.get(key)
        next_ver = (prev.version + 1) if prev is not None else 1
        entry = ArtifactEntry(
            key=key,
            tenant_id=tenant_id,
            version=next_ver,
            payload=payload,
            updated_at=datetime.now(UTC),
        )
        entry.checksum_sha256 = entry.compute_checksum()
        tenant_bucket[key] = entry
        return entry

    def get_artifact(self, *, tenant_id: str, key: str) -> ArtifactEntry | None:
        entry = self._artifacts.get(tenant_id, {}).get(key)
        if entry is not None and self.enforce_integrity:
            entry.verify_integrity()
        return entry

    # ------------------------------------------------------------------
    # Context Assembly across all 4 layers within token budget
    # ------------------------------------------------------------------
    async def assemble_context(
        self,
        query: str,
        *,
        tenant_id: str,
        token_budget: int | None = None,
        semantic_top_k: int = 3,
        episodic_limit: int = 3,
    ) -> AssembledContext:
        """Assemble system + episodic + semantic + compacted + working messages within budget."""
        budget = max(128, int(token_budget or self.max_working_tokens))
        messages: list[Message] = []
        used = 0

        # 1. Pinned system instructions
        for msg in self._working_messages:
            if msg.role == Role.SYSTEM:
                messages.append(msg)
                used += estimate_tokens(msg.content)

        # 2. Episodic memory hits
        episodes = self.get_episodes(tenant_id=tenant_id, limit=episodic_limit)
        if episodes:
            ep_lines = "\n".join(
                f"- [{e.stage} / {e.outcome}] {e.summary}" for e in episodes
            )
            ep_block = f"## Episodic Memory (Prior Runs)\n{ep_lines}"
            ep_tokens = estimate_tokens(ep_block)
            if used + ep_tokens <= budget:
                messages.append(Message(role=Role.SYSTEM, content=ep_block, cache_control=True))
                used += ep_tokens

        # 3. Hybrid semantic knowledge retrieval
        sem_results = await self.search_semantic(
            query, tenant_id=tenant_id, top_k=semantic_top_k
        )
        retrieved_chunks: list[SemanticChunk] = []
        if sem_results:
            chunk_lines: list[str] = []
            for chunk, _score in sem_results:
                line = f"- ({chunk.source_id}) {chunk.content}"
                if used + estimate_tokens(line) <= budget:
                    chunk_lines.append(line)
                    retrieved_chunks.append(chunk)
                    used += estimate_tokens(line)
            if chunk_lines:
                messages.append(
                    Message(
                        role=Role.SYSTEM,
                        content="## Semantic Knowledge Context\n" + "\n".join(chunk_lines),
                    )
                )

        # 4. Compacted working history (if any turns were compacted)
        if self._compacted_summaries:
            compacted_text = "## Compacted Conversation History\n" + "\n".join(
                self._compacted_summaries[-8:]
            )
            c_tokens = estimate_tokens(compacted_text)
            if used + c_tokens <= budget:
                messages.append(Message(role=Role.SYSTEM, content=compacted_text))
                used += c_tokens

        # 5. Active working turns (most recent prioritized within remaining budget)
        active_turns = [m for m in self._working_messages if m.role != Role.SYSTEM]
        selected_turns: list[Message] = []
        for turn in reversed(active_turns):
            t_tokens = estimate_tokens(turn.content)
            if used + t_tokens <= budget or not selected_turns:
                selected_turns.append(turn)
                used += t_tokens
        selected_turns.reverse()
        messages.extend(selected_turns)

        return AssembledContext(
            messages=messages,
            used_tokens=used,
            token_budget=budget,
            compacted_turns=self._compacted_turn_count,
            retrieved_chunks=retrieved_chunks,
            episodic_hits=episodes,
        )
