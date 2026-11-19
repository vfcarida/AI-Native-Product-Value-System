# API Reference: Simulation

**Module**: `product_value_system.simulation`  
**Source**: [src/product_value_system/simulation/](../../../src/product_value_system/simulation/)

Synthetic customer persona simulation powered by Microsoft TinyTroupe (`tinytroupe>=0.2.0`), with a lightweight local fallback engine (`LocalTinyPerson` / `LocalTinyWorld`) for offline testing and PVS `ModelAdapter`-backed runs.

> **Synthetic Data Disclaimer**: Synthetic customer personas are NOT real users. Simulation outputs are intended for rapid hypothesis exploration and risk discovery before real user research—never as a sole decision-making basis.

---

## `TinyTroupeAdapter`

**File**: `src/product_value_system/simulation/tinytroupe_adapter.py`

### Constructor

```python
TinyTroupeAdapter(
    llm_adapter: ModelAdapter | None = None,
    max_cost_usd: float = 100.0,
    enable_caching: bool = True,
    require_tinytroupe: bool = False,
)
```

- `llm_adapter`: Optional PVS `ModelAdapter` (`OpenAIAdapter`, `AnthropicAdapter`, `MockModelAdapter`).
- `max_cost_usd`: Hard USD cost cap; raises `BudgetExceededError` before/during scenarios if exceeded.
- `require_tinytroupe`: When `True`, raises `ImportError` if the optional `tinytroupe` package is not installed.

### Methods

#### `create_persona(definition: PersonaDefinition) -> Any`
Converts a `PersonaDefinition` into a `TinyPerson` (or `LocalTinyPerson`) configured with demographics, Big-Five personality traits, behaviors, context awareness, and optional accessibility needs.

#### `create_personas(definitions: list[PersonaDefinition]) -> list[Any]`
Creates a list of persona instances from definitions.

#### `async run_scenario(scenario_name: str, personas: list[Any], scenario_config: dict[str, Any]) -> SimulationResult`
Executes all configured `interactions` across `personas`, enforcing `max_cost_usd`, and returns a `SimulationResult` containing raw interactions, aggregated insights (`themes`, `sentiment`, `pain_points`, `feature_requests`, `response_count`), quality metrics (`persona_adherence`, `interaction_realism`, `response_diversity`), cost, duration, and synthetic-data disclaimer.

---

## `PersonaFactory`

**File**: `src/product_value_system/simulation/persona_factory.py`

```python
factory = PersonaFactory("config/simulation/persona_templates.yaml", seed=42)
early_adopters = factory.generate_population("tech_early_adopter", size=50, diversity_factor=0.3)
mixed = factory.generate_mixed_population(
    {"tech_early_adopter": 0.3, "mainstream_user": 0.5, "accessibility_focused": 0.2},
    total_size=100,
)
```

---

## `PopulationGenerator`

**File**: `src/product_value_system/simulation/population_generator.py`

Generates stratified demographic populations from `PopulationSpec` and `DemographicSegment` objects, and computes audit summaries via `summarize_population(population)`.

---

## `SimulationValidator`

**File**: `src/product_value_system/simulation/simulation_validator.py`

Loads thresholds from `config/simulation/validation_criteria.yaml` and validates `SimulationResult`:

- `compare_distributions(simulated_scores, real_scores) -> list[StatisticalTestResult]`: Runs Welch's two-sample t-test (`t_test`) and two-sample Kolmogorov-Smirnov test (`ks_test`) via `scipy.stats`.
- `validate(result, simulated_scores=None, real_scores=None) -> ValidationReport`: Evaluates sample-size minimums, quality thresholds (`persona_adherence`, `interaction_realism`, `response_diversity`), optional empirical tests, and calibrated confidence (`high` / `medium` / `low`).

---

## `ScenarioRunner`

**File**: `src/product_value_system/simulation/scenario_runner.py`

- `async run_scenario(scenario_path: Path | str) -> SimulationResult`: Loads a scenario YAML (e.g., `examples/simulation/focus_group.yaml`), generates its population, and runs the scenario.
- `async run_hypothesis_validation(outcome_hypothesis, problem_context, population_size=None) -> SimulationResult`: Infers a domain-appropriate persona mix and executes a 3-round validation script against a `DiscoveryAgent` outcome hypothesis.

---

## CLI Usage

```bash
# Run a standalone synthetic customer simulation scenario
pvs run-simulation examples/simulation/ad_evaluation.yaml --max-cost 5.0

# Run Discovery + Value Stream with optional simulation validation enabled
pvs run examples/greenfield/problem_payment_flow.yaml --enable-simulation --simulation-budget 10.0
```
