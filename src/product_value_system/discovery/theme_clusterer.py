"""
Semantic theme clustering across multiple customer interviews (EVO-022).

Uses cosine-distance DBSCAN (via scikit-learn when installed, with pure-numpy fallback)
and supports custom async/sync embedding models or a built-in semantic + TF-IDF
embedding generator for deterministic offline clustering.
"""

from __future__ import annotations

import inspect
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from product_value_system.discovery.insight_extractor import (
    SEVERITY_RANK,
    LLMCallable,
    PainPoint,
    PainPointSeverity,
)

EmbeddingCallable = Callable[[list[str]], Awaitable[list[list[float]]] | list[list[float]]]

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "with",
        "too",
        "very",
        "just",
        "takes",
        "take",
        "need",
        "dont",
        "don",
        "never",
        "often",
    }
)

# Domain semantic concept groups so paraphrases ("takes forever", "too slow", "waiting too long",
# "need faster") map to high cosine similarity even without an external embedding API.
_SEMANTIC_CONCEPTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Transfer Speed",
        re.compile(
            r"\b(slow|forever|wait\w*|faster|speed|latency|second\w*|delay\w*|lag\w*|timeout|long)\b",
            re.I,
        ),
    ),
    (
        "Error Handling",
        re.compile(
            r"\b(error\w*|fail\w*|unclear|confus\w*|message\w*|bug\w*|broken|wrong|next step\w*)\b",
            re.I,
        ),
    ),
    (
        "Biometric Friction",
        re.compile(
            r"\b(biometric\w*|fingerprint\w*|face\s*id|face\s*recognition|auth\w*|login|verify|verification)\b",
            re.I,
        ),
    ),
    (
        "Workflow Complexity",
        re.compile(
            r"\b(too many steps|multiple steps|extra steps|clicks?|confirm \d+ times|"
            r"complex\w*|manual\w*|tedious|simplicity|simple|menu\w*|navigation)\b",
            re.I,
        ),
    ),
    (
        "Trust & Security",
        re.compile(
            r"\b(trust|secur\w*|safe\w*|privacy|fraud|scam|scared|worried|confidence)\b",
            re.I,
        ),
    ),
    (
        "Pricing & Fees",
        re.compile(
            r"\b(fee\w*|price\w*|cost\w*|expensive|charge\w*|interest\s+rate|exchange\s+rate)\b",
            re.I,
        ),
    ),
    (
        "Notifications & Status",
        re.compile(r"\b(notif\w*|alert\w*|status|receipt\w*|track\w*|update\w*)\b", re.I),
    ),
]


def default_semantic_embeddings(texts: list[str]) -> list[list[float]]:
    """Deterministic semantic-concept + TF-IDF embedding model for offline clustering."""
    tokenized = [re.findall(r"[a-z0-9]+", t.lower()) for t in texts]
    vocab = sorted({tok for tokens in tokenized for tok in tokens if tok not in _STOPWORDS})
    vocab_index = {tok: idx for idx, tok in enumerate(vocab)}
    n_docs = max(1, len(texts))

    df = Counter(tok for tokens in tokenized for tok in set(tokens) if tok in vocab_index)
    concept_dim = len(_SEMANTIC_CONCEPTS)
    total_dim = concept_dim + len(vocab)
    if total_dim == 0:
        return [[1.0] for _ in texts]

    vectors: list[list[float]] = []
    for text, tokens in zip(texts, tokenized, strict=True):
        concept_vec = np.zeros(concept_dim, dtype=float)
        for c_idx, (_, pattern) in enumerate(_SEMANTIC_CONCEPTS):
            matches = pattern.findall(text)
            if matches:
                concept_vec[c_idx] = 1.0 + 0.25 * min(len(matches) - 1, 3)

        tfidf_vec = np.zeros(len(vocab), dtype=float)
        counts = Counter(tok for tok in tokens if tok in vocab_index)
        for tok, tf in counts.items():
            idf = math.log((1 + n_docs) / (1 + df[tok])) + 1.0
            tfidf_vec[vocab_index[tok]] = tf * idf

        c_norm = float(np.linalg.norm(concept_vec))
        t_norm = float(np.linalg.norm(tfidf_vec))
        if c_norm > 0:
            concept_vec = concept_vec / c_norm
        if t_norm > 0:
            tfidf_vec = tfidf_vec / t_norm

        if c_norm > 0 and t_norm > 0:
            # Weight semantic concept subspace (0.90) vs lexical TF-IDF (0.435) so
            # paraphrases sharing a concept achieve >=0.81 cosine similarity while
            # distinct concepts remain well-separated (<0.10) even with shared nouns.
            combined = np.concatenate([0.90 * concept_vec, 0.435 * tfidf_vec])
        elif c_norm > 0:
            combined = np.concatenate([concept_vec, tfidf_vec])
        else:
            combined = np.concatenate([concept_vec, tfidf_vec])

        norm = float(np.linalg.norm(combined))
        if norm > 0:
            combined = combined / norm
        vectors.append(combined.tolist())

    return vectors


def domain_agnostic_tfidf_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Pure domain-agnostic word + character trigram TF-IDF embeddings (EVO-P3-11).
    Contains zero hardcoded industry/fintech regexes so clustering generalizes
    across healthcare, SaaS, logistics, e-commerce, and developer tools.
    """
    if not texts:
        return []
    tokenized = [
        [tok for tok in re.findall(r"[a-z0-9]{2,}", t.lower()) if tok not in _STOPWORDS]
        for t in texts
    ]
    trigrams_per_doc = [
        [
            " ".join(t.lower().split())[i : i + 3]
            for i in range(max(0, len(" ".join(t.lower().split())) - 2))
        ]
        for t in texts
    ]
    word_vocab = sorted({tok for tokens in tokenized for tok in tokens})
    tri_vocab = sorted({tri for tris in trigrams_per_doc for tri in tris})
    w_idx = {tok: i for i, tok in enumerate(word_vocab)}
    t_idx = {tri: i for i, tri in enumerate(tri_vocab)}
    n_docs = len(texts)

    w_df = Counter(tok for tokens in tokenized for tok in set(tokens))
    t_df = Counter(tri for tris in trigrams_per_doc for tri in set(tris))
    if not word_vocab and not tri_vocab:
        return [[1.0] for _ in texts]

    vectors: list[list[float]] = []
    for tokens, tris in zip(tokenized, trigrams_per_doc, strict=True):
        w_vec = np.zeros(len(word_vocab), dtype=float)
        for tok, count in Counter(tokens).items():
            idf = math.log((1 + n_docs) / (1 + w_df[tok])) + 1.0
            w_vec[w_idx[tok]] = (1.0 + math.log(count)) * idf
        w_norm = float(np.linalg.norm(w_vec))
        if w_norm > 0:
            w_vec = w_vec / w_norm

        t_vec = np.zeros(len(tri_vocab), dtype=float)
        for tri, count in Counter(tris).items():
            idf = math.log((1 + n_docs) / (1 + t_df[tri])) + 1.0
            t_vec[t_idx[tri]] = (1.0 + math.log(count)) * idf
        t_norm = float(np.linalg.norm(t_vec))
        if t_norm > 0:
            t_vec = t_vec / t_norm

        combined = np.concatenate([0.82 * w_vec, 0.57 * t_vec])
        norm = float(np.linalg.norm(combined))
        if norm > 0:
            combined = combined / norm
        vectors.append(combined.tolist())
    return vectors


def _cosine_dbscan_numpy(
    embeddings: np.ndarray,
    eps: float,
    min_samples: int,
) -> np.ndarray:
    """Pure-numpy cosine-distance DBSCAN fallback."""
    n = embeddings.shape[0]
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normalized = embeddings / norms
    sim_matrix = np.clip(normalized @ normalized.T, -1.0, 1.0)
    dist_matrix = 1.0 - sim_matrix

    labels = np.full(n, -1, dtype=int)
    visited = np.zeros(n, dtype=bool)
    cluster_id = 0

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        neighbors = list(np.where(dist_matrix[i] <= eps)[0])
        if len(neighbors) < min_samples:
            continue
        labels[i] = cluster_id
        queue = list(neighbors)
        while queue:
            pt = queue.pop(0)
            if not visited[pt]:
                visited[pt] = True
                pt_neighbors = list(np.where(dist_matrix[pt] <= eps)[0])
                if len(pt_neighbors) >= min_samples:
                    queue.extend(nb for nb in pt_neighbors if nb not in queue)
            if labels[pt] == -1:
                labels[pt] = cluster_id
        cluster_id += 1

    return labels


@dataclass
class ClusteredTheme:
    """Cluster of semantically related customer pain points across interviews."""

    theme_name: str
    pain_points: list[PainPoint]
    frequency: int  # Unique interview count
    representative_quote: str
    interview_ids: list[str] = field(default_factory=list)
    total_interviews: int = 0
    highest_severity: PainPointSeverity = PainPointSeverity.MEDIUM
    avg_confidence: float = 0.0

    @property
    def prevalence_pct(self) -> float:
        if self.total_interviews <= 0:
            return 0.0
        return round((self.frequency / self.total_interviews) * 100.0, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme_name": self.theme_name,
            "frequency": self.frequency,
            "total_interviews": self.total_interviews,
            "prevalence_pct": self.prevalence_pct,
            "interview_ids": list(self.interview_ids),
            "highest_severity": self.highest_severity.value,
            "avg_confidence": round(self.avg_confidence, 4),
            "representative_quote": self.representative_quote,
            "pain_points": [p.to_dict() for p in self.pain_points],
        }


class ThemeClusterer:
    """Cluster similar customer pain points across interviews using cosine similarity."""

    def __init__(
        self,
        embedding_model: EmbeddingCallable | None = None,
        llm_callable: LLMCallable | None = None,
        *,
        embedding_adapter: Any | None = None,
        domain_agnostic: bool = False,
    ) -> None:
        self.domain_agnostic = bool(domain_agnostic)
        if embedding_adapter is not None:
            self.embedding_model: EmbeddingCallable = embedding_adapter.embed_batch
        elif embedding_model is not None:
            self.embedding_model = embedding_model
        elif self.domain_agnostic:
            self.embedding_model = domain_agnostic_tfidf_embeddings
        else:
            self.embedding_model = default_semantic_embeddings
        self.llm = llm_callable

    async def cluster_pain_points(
        self,
        pain_points: list[PainPoint],
        similarity_threshold: float = 0.7,
        *,
        min_samples: int = 2,
        total_interviews: int | None = None,
        include_unclustered: bool = False,
    ) -> list[ClusteredTheme]:
        """Cluster semantically similar pain points using DBSCAN over cosine distance."""
        if not pain_points:
            return []

        unique_all_interviews = {p.interview_id for p in pain_points if p.interview_id}
        resolved_total = (
            total_interviews if total_interviews is not None else max(1, len(unique_all_interviews))
        )

        descriptions = [p.description for p in pain_points]
        embeddings = await self._get_embeddings(descriptions)

        eps = max(0.0, min(2.0, 1.0 - similarity_threshold))
        labels = self._run_dbscan(embeddings, eps=eps, min_samples=min_samples)

        clusters: dict[int, list[PainPoint]] = {}
        noise_points: list[PainPoint] = []
        for idx, label in enumerate(labels):
            if int(label) == -1:
                noise_points.append(pain_points[idx])
                continue
            clusters.setdefault(int(label), []).append(pain_points[idx])

        clustered_themes: list[ClusteredTheme] = []
        for points in clusters.values():
            clustered_themes.append(
                await self._build_clustered_theme(points, resolved_total)
            )

        if include_unclustered:
            for point in noise_points:
                clustered_themes.append(
                    await self._build_clustered_theme([point], resolved_total)
                )

        clustered_themes.sort(
            key=lambda t: (t.frequency, SEVERITY_RANK[t.highest_severity], len(t.pain_points)),
            reverse=True,
        )
        return clustered_themes

    def _run_dbscan(
        self,
        embeddings: np.ndarray,
        *,
        eps: float,
        min_samples: int,
    ) -> np.ndarray:
        try:
            from sklearn.cluster import DBSCAN

            clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit(embeddings)
            return np.asarray(clustering.labels_, dtype=int)
        except Exception:
            return _cosine_dbscan_numpy(embeddings, eps=eps, min_samples=min_samples)

    async def _build_clustered_theme(
        self,
        points: list[PainPoint],
        total_interviews: int,
    ) -> ClusteredTheme:
        theme_name = await self._generate_theme_name(points)
        rep_quote = max(
            (q for p in points for q in p.quotes),
            key=len,
            default=points[0].description,
        )
        interview_ids = sorted({p.interview_id for p in points if p.interview_id})
        highest_sev = max(points, key=lambda p: SEVERITY_RANK[p.severity]).severity
        avg_conf = sum(p.confidence for p in points) / len(points)

        return ClusteredTheme(
            theme_name=theme_name,
            pain_points=points,
            frequency=len(interview_ids) or len(points),
            representative_quote=rep_quote,
            interview_ids=interview_ids,
            total_interviews=total_interviews,
            highest_severity=highest_sev,
            avg_confidence=avg_conf,
        )

    async def _get_embeddings(self, texts: list[str]) -> np.ndarray:
        result = self.embedding_model(texts)
        if inspect.isawaitable(result):
            resolved = await result
        else:
            resolved = result
        return np.asarray(resolved, dtype=float)

    async def _generate_theme_name(self, pain_points: list[PainPoint]) -> str:
        if self.llm is not None:
            descriptions = "\n".join(f"- {p.description}" for p in pain_points)
            prompt = (
                "Generate a short, descriptive theme name (2-5 words) that captures "
                f"these related customer pain points:\n\n{descriptions}\n\n"
                "Output just the theme name, nothing else."
            )
            result = self.llm(prompt, temperature=0.2)
            name = str(await result if inspect.isawaitable(result) else result).strip().strip('"')
            if name:
                return name

        # Check dominant semantic concept match first (legacy mode only)
        if not self.domain_agnostic:
            combined = " ".join(p.description for p in pain_points)
            concept_scores: list[tuple[int, str]] = []
            for concept_name, pattern in _SEMANTIC_CONCEPTS:
                hits = len(pattern.findall(combined))
                if hits > 0:
                    concept_scores.append((hits, concept_name))
            if concept_scores:
                concept_scores.sort(key=lambda pair: pair[0], reverse=True)
                return concept_scores[0][1]

        # Fallback: top non-stopword tokens
        words = [
            tok
            for p in pain_points
            for tok in re.findall(r"[a-zA-Z]{3,}", p.description.lower())
            if tok not in _STOPWORDS
        ]
        if not words:
            return "Customer Feedback Theme"
        common = [word.title() for word, _ in Counter(words).most_common(3)]
        return " ".join(common)
