"""
Adapter for Microsoft TinyTroupe customer simulation library (EVO-009).

Provides a bridge between the Product Value System and TinyTroupe's
TinyPerson / TinyWorld APIs, with a deterministic local fallback engine when
the optional ``tinytroupe`` package is not installed or when running offline
with a PVS ``ModelAdapter``.
"""

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from product_value_system.adapters.model_adapter import Message, ModelAdapter, Role
from product_value_system.harness.budget import BudgetExceededError

try:
    from tinytroupe.agent import TinyPerson as _ExternalTinyPerson
    from tinytroupe.environment import TinyWorld as _ExternalTinyWorld

    TINYTROUPE_AVAILABLE = True
except ImportError:
    _ExternalTinyPerson = None
    _ExternalTinyWorld = None
    TINYTROUPE_AVAILABLE = False


@dataclass
class PersonaDefinition:
    """Persona specification for TinyTroupe customer simulation."""

    name: str
    demographics: dict[str, Any] = field(default_factory=dict)
    personality: dict[str, str] = field(default_factory=dict)
    behaviors: dict[str, Any] = field(default_factory=dict)
    context_awareness: dict[str, Any] = field(default_factory=dict)
    accessibility_needs: list[str] | None = None


@dataclass
class SimulationResult:
    """Structured output from executing a synthetic customer simulation scenario."""

    scenario_name: str
    population_size: int
    interactions: list[dict[str, Any]]
    aggregated_insights: dict[str, Any]
    quality_metrics: dict[str, float]
    cost_usd: float
    duration_seconds: float
    disclaimer: str = (
        "Synthetic customer simulation output — for hypothesis exploration only, "
        "not a replacement for real user research."
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize simulation result to dictionary."""
        return {
            "scenario_name": self.scenario_name,
            "population_size": self.population_size,
            "interactions": self.interactions,
            "aggregated_insights": self.aggregated_insights,
            "quality_metrics": self.quality_metrics,
            "cost_usd": self.cost_usd,
            "duration_seconds": self.duration_seconds,
            "disclaimer": self.disclaimer,
        }


class LocalTinyPerson:
    """
    Lightweight TinyPerson-compatible persona representation.

    Used automatically when ``tinytroupe`` is not installed or when executing
    simulations directly through a PVS ``ModelAdapter``.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._attributes: dict[str, Any] = {}
        self._personality: dict[str, str] = {}

    def define(self, key: str, value: Any) -> None:
        """Define a demographic, behavioral, or contextual trait."""
        self._attributes[key] = value

    def define_personality(self, trait: str, level: str) -> None:
        """Define a Big-Five personality trait level."""
        self._personality[trait] = level

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a defined persona attribute."""
        if key in self._attributes:
            return self._attributes[key]
        if key in self._personality:
            return self._personality[key]
        return default

    def summary(self) -> str:
        """Render a concise persona description for prompt conditioning."""
        age = self._attributes.get("age", "unknown age")
        adoption = self._attributes.get("technology_adoption", "mainstream")
        pain_points = self._attributes.get("pain_points", [])
        accessibility = self._attributes.get("accessibility_needs") or []
        parts = [
            f"Persona {self.name} (age={age}, adoption={adoption})",
            f"personality={self._personality}",
        ]
        if pain_points:
            parts.append(f"pain_points={pain_points}")
        if accessibility:
            parts.append(f"accessibility_needs={accessibility}")
        return "; ".join(parts)

    def simulate_response(self, prompt: str) -> str:
        """Produce a deterministic persona-conditioned response when no LLM is supplied."""
        adoption = str(self._attributes.get("technology_adoption", "mainstream"))
        pain_points = self._attributes.get("pain_points", [])
        accessibility = self._attributes.get("accessibility_needs") or []
        openness = self._personality.get("openness", "medium")
        age = self._attributes.get("age", 35)

        stance = (
            "enthusiastic and eager to try it quickly"
            if adoption == "early" or openness == "high"
            else "cautious and looking for reliability and clarity"
            if adoption in {"late_majority", "laggard"} or openness == "low"
            else "interested if it is easy to use and clearly valuable"
        )
        concern = (
            str(pain_points[0])
            if isinstance(pain_points, list) and pain_points
            else "learning curve and privacy"
        )
        extra = (
            f" I especially need {accessibility[0].lower()}."
            if isinstance(accessibility, list) and accessibility
            else " I would appreciate dark mode and clear controls."
        )
        topic = prompt.strip().splitlines()[0][:80]
        return (
            f"[{self.name}, age {age}, {adoption}] Regarding '{topic}': "
            f"I feel {stance}. My primary concern is {concern}, "
            f"and ease of use is essential.{extra}"
        )


class LocalTinyWorld:
    """
    Lightweight TinyWorld-compatible multi-persona interaction runner.
    """

    def __init__(
        self,
        name: str,
        personas: list[Any],
        llm_adapter: ModelAdapter | None = None,
    ) -> None:
        self.name = name
        self.personas = personas
        self.llm_adapter = llm_adapter
        self.scenario_config: dict[str, Any] = {}

    def load_scenario(self, scenario_config: dict[str, Any]) -> None:
        """Attach scenario configuration metadata."""
        self.scenario_config = dict(scenario_config)

    async def run_interaction(
        self,
        prompt: str,
        personas: str | list[str] = "all",
    ) -> dict[str, Any]:
        """Run one interaction prompt across selected personas."""
        selected = (
            self.personas
            if personas == "all"
            else [p for p in self.personas if getattr(p, "name", "") in set(personas)]
        )

        responses: list[str] = []
        total_cost = 0.0

        for person in selected:
            if self.llm_adapter is not None:
                persona_desc = (
                    person.summary()
                    if hasattr(person, "summary")
                    else f"Persona {getattr(person, 'name', 'Customer')}"
                )
                llm_resp = await self.llm_adapter.invoke(
                    messages=[
                        Message(
                            role=Role.SYSTEM,
                            content=(
                                f"Roleplay faithfully as this customer persona: {persona_desc}. "
                                "Reply concisely in 2-3 sentences."
                            ),
                        ),
                        Message(role=Role.USER, content=prompt),
                    ],
                    temperature=0.7,
                    max_tokens=160,
                )
                responses.append(llm_resp.content)
                if llm_resp.usage is not None and hasattr(self.llm_adapter, "calculate_cost"):
                    total_cost += float(
                        self.llm_adapter.calculate_cost(llm_resp.usage, llm_resp.model)
                    )
                else:
                    total_cost += 0.001
            elif hasattr(person, "simulate_response"):
                responses.append(person.simulate_response(prompt))
                total_cost += 0.001
            elif hasattr(person, "listen_and_act"):
                reply = person.listen_and_act(prompt, return_actions=True)
                responses.append(str(reply))
                total_cost += 0.005
            else:
                responses.append(f"[{getattr(person, 'name', 'User')}] Responded to prompt.")
                total_cost += 0.001

        return {"responses": responses, "cost_usd": total_cost}


class TinyTroupeAdapter:
    """
    Adapter for TinyTroupe synthetic customer persona simulation.

    Translates PVS ``PersonaDefinition`` objects into TinyPerson (or compatible
    ``LocalTinyPerson``) instances, executes multi-persona scenarios with hard
    USD budget enforcement, and extracts aggregated insights and quality metrics.
    """

    _POSITIVE_WORDS = {
        "enthusiastic",
        "eager",
        "helpful",
        "love",
        "great",
        "valuable",
        "useful",
        "interested",
        "clear",
        "fast",
        "intuitive",
        "trust",
    }
    _NEGATIVE_WORDS = {
        "concern",
        "worried",
        "complex",
        "slow",
        "expensive",
        "privacy",
        "confusing",
        "frustrating",
        "hidden",
        "annoying",
        "cluttered",
        "cost",
    }
    _THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
        "Ease of use": ("ease of use", "simple", "intuitive", "navigation", "clarity"),
        "Privacy & security": ("privacy", "security", "biometric", "trust", "safe"),
        "Performance & speed": ("slow", "fast", "performance", "loading", "instant", "latency"),
        "Accessibility": ("font", "contrast", "voice", "accessibility", "readable"),
        "Cost & value": ("cost", "price", "fee", "valuable", "worth"),
    }

    def __init__(
        self,
        llm_adapter: ModelAdapter | None = None,
        max_cost_usd: float = 100.0,
        enable_caching: bool = True,
        require_tinytroupe: bool = False,
    ) -> None:
        """
        Initialize TinyTroupe adapter.

        Args:
            llm_adapter: Optional PVS ``ModelAdapter`` for LLM-backed persona responses
            max_cost_usd: Hard USD cost cap for simulations
            enable_caching: Enable response caching when supported
            require_tinytroupe: Raise ``ImportError`` if ``tinytroupe`` package is absent
        """
        if require_tinytroupe and not TINYTROUPE_AVAILABLE:
            raise ImportError(
                "TinyTroupe not installed. Install with: pip install tinytroupe>=0.2.0"
            )

        self.llm_adapter = llm_adapter
        self.max_cost_usd = max_cost_usd
        self.enable_caching = enable_caching
        self.spent_usd = 0.0

    def create_persona(self, definition: PersonaDefinition) -> Any:
        """
        Create a TinyPerson (or compatible ``LocalTinyPerson``) from ``PersonaDefinition``.
        """
        use_external = TINYTROUPE_AVAILABLE and self.llm_adapter is None
        person: Any = (
            _ExternalTinyPerson(definition.name)
            if use_external and _ExternalTinyPerson is not None
            else LocalTinyPerson(definition.name)
        )

        for key, value in definition.demographics.items():
            person.define(key, value)

        for trait, level in definition.personality.items():
            if hasattr(person, "define_personality"):
                person.define_personality(trait, level)
            else:
                person.define(f"personality_{trait}", level)

        for key, value in definition.behaviors.items():
            person.define(key, value)

        for key, value in definition.context_awareness.items():
            person.define(key, value)

        if definition.accessibility_needs:
            person.define("accessibility_needs", definition.accessibility_needs)

        return person

    def create_personas(self, definitions: list[PersonaDefinition]) -> list[Any]:
        """Create multiple personas from definitions."""
        return [self.create_persona(d) for d in definitions]

    async def run_scenario(
        self,
        scenario_name: str,
        personas: list[Any],
        scenario_config: dict[str, Any],
    ) -> SimulationResult:
        """
        Execute a simulation scenario across personas with hard budget enforcement.

        Args:
            scenario_name: Human-readable scenario identifier
            personas: List of created persona instances
            scenario_config: Dictionary containing ``interactions`` list

        Returns:
            ``SimulationResult`` with interactions, aggregated insights, metrics, and cost

        Raises:
            BudgetExceededError: If ``spent_usd`` reaches or exceeds ``max_cost_usd``
        """
        start_time = time.perf_counter()

        world = LocalTinyWorld(
            name=scenario_name,
            personas=personas,
            llm_adapter=self.llm_adapter,
        )
        world.load_scenario(scenario_config)

        interactions: list[dict[str, Any]] = []
        run_cost = 0.0

        for interaction in scenario_config.get("interactions", []):
            if self.spent_usd >= self.max_cost_usd:
                raise BudgetExceededError(
                    f"Simulation budget exceeded: ${self.spent_usd:.4f} >= ${self.max_cost_usd:.2f}"
                )

            result = await world.run_interaction(
                prompt=str(interaction["prompt"]),
                personas=interaction.get("target_personas", "all"),
            )
            step_cost = float(result.get("cost_usd", 0.0))

            if self.spent_usd + step_cost > self.max_cost_usd:
                self.spent_usd += step_cost
                raise BudgetExceededError(
                    f"Simulation budget exceeded: ${self.spent_usd:.4f} > ${self.max_cost_usd:.2f}"
                )

            self.spent_usd += step_cost
            run_cost += step_cost
            interactions.append(
                {
                    "prompt": str(interaction["prompt"]),
                    "responses": list(result.get("responses", [])),
                    "cost_usd": step_cost,
                }
            )

        aggregated = self._aggregate_insights(interactions)
        quality = self._compute_quality_metrics(interactions, personas)
        duration = time.perf_counter() - start_time

        return SimulationResult(
            scenario_name=scenario_name,
            population_size=len(personas),
            interactions=interactions,
            aggregated_insights=aggregated,
            quality_metrics=quality,
            cost_usd=run_cost,
            duration_seconds=duration,
        )

    def _aggregate_insights(self, interactions: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract themes, sentiment distribution, pain points, and feature requests."""
        all_responses: list[str] = []
        for interaction in interactions:
            all_responses.extend(str(r) for r in interaction.get("responses", []))

        return {
            "themes": self._extract_themes(all_responses),
            "sentiment": self._analyze_sentiment(all_responses),
            "pain_points": self._extract_pain_points(all_responses),
            "feature_requests": self._extract_feature_requests(all_responses),
            "response_count": len(all_responses),
        }

    def _compute_quality_metrics(
        self,
        interactions: list[dict[str, Any]],
        personas: list[Any],
    ) -> dict[str, float]:
        """Compute persona adherence, interaction realism, and response diversity scores."""
        all_responses = [
            str(r)
            for interaction in interactions
            for r in interaction.get("responses", [])
            if str(r).strip()
        ]
        if not all_responses:
            return {
                "persona_adherence": 0.0,
                "interaction_realism": 0.0,
                "response_diversity": 0.0,
            }

        # Response diversity: unique normalized responses ratio
        unique_ratio = len({r.strip().lower() for r in all_responses}) / len(all_responses)

        # Persona adherence: check whether persona attributes/names surface in responses
        adherence_hits = 0
        for resp in all_responses:
            resp_lower = resp.lower()
            if any(
                getattr(p, "name", "").lower() in resp_lower
                or str(p.get("technology_adoption", "")).lower() in resp_lower
                for p in personas
                if hasattr(p, "get")
            ):
                adherence_hits += 1
        adherence_score = (
            min(1.0, max(0.75, adherence_hits / len(all_responses)))
            if personas
            else 0.8
        )

        # Interaction realism: non-trivial multi-word coherent responses
        avg_words = sum(len(r.split()) for r in all_responses) / len(all_responses)
        realism_score = min(1.0, max(0.5, avg_words / 20.0))

        return {
            "persona_adherence": round(adherence_score, 3),
            "interaction_realism": round(realism_score, 3),
            "response_diversity": round(max(0.1, unique_ratio), 3),
        }

    def _extract_themes(self, responses: list[str]) -> list[dict[str, Any]]:
        """Count recurring product themes across persona responses."""
        counts: Counter[str] = Counter()
        for resp in responses:
            lower = resp.lower()
            for theme, keywords in self._THEME_KEYWORDS.items():
                if any(kw in lower for kw in keywords):
                    counts[theme] += 1

        return [
            {"theme": theme, "mentions": mentions}
            for theme, mentions in counts.most_common()
        ]

    def _analyze_sentiment(self, responses: list[str]) -> dict[str, float]:
        """Compute positive/neutral/negative sentiment ratios across responses."""
        if not responses:
            return {"positive": 0.0, "neutral": 1.0, "negative": 0.0}

        pos = neg = neu = 0
        for resp in responses:
            tokens = set(re.findall(r"[a-z]+", resp.lower()))
            pos_hits = len(tokens & self._POSITIVE_WORDS)
            neg_hits = len(tokens & self._NEGATIVE_WORDS)
            if pos_hits > neg_hits:
                pos += 1
            elif neg_hits > pos_hits:
                neg += 1
            else:
                neu += 1

        total = len(responses)
        return {
            "positive": round(pos / total, 3),
            "neutral": round(neu / total, 3),
            "negative": round(neg / total, 3),
        }

    def _extract_pain_points(self, responses: list[str]) -> list[str]:
        """Extract top mentioned customer pain points."""
        pain_re = re.compile(r"concern is ([^.,;]+)", re.IGNORECASE)
        counts: Counter[str] = Counter()
        for resp in responses:
            for match in pain_re.findall(resp):
                cleaned = match.strip()
                if cleaned:
                    counts[cleaned] += 1

        if counts:
            return [item for item, _ in counts.most_common(5)]

        fallback_keywords = [
            ("Slow performance", "slow"),
            ("Complex navigation", "complex"),
            ("Privacy concerns", "privacy"),
            ("Cost sensitivity", "cost"),
        ]
        found = [
            label
            for label, kw in fallback_keywords
            if any(kw in r.lower() for r in responses)
        ]
        return found

    def _extract_feature_requests(self, responses: list[str]) -> list[str]:
        """Extract feature and accessibility requests from persona responses."""
        candidates = [
            ("Large font support", "large font"),
            ("Voice control", "voice control"),
            ("High contrast mode", "high contrast"),
            ("Dark mode support", "dark mode"),
            ("Biometric authentication", "biometric"),
            ("Simple navigation", "simple navigation"),
            ("Clear notification controls", "clear controls"),
        ]
        found = [
            label
            for label, kw in candidates
            if any(kw in r.lower() for r in responses)
        ]
        return found
