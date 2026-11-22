# Zero-Cost Runnable Showcase Tutorials

These three standalone tutorials run completely offline at **$0.00 API cost** with zero external infrastructure required:

| Showcase | Subsystems Exercised | Run Command |
|---|---|---|
| **1. FinOps & Resilience** | Complexity Model Router + Prompt Cache + Batch Eval Savings + Circuit Breaker | `python examples/showcases/finops_optimization_demo.py` |
| **2. Brownfield Modernization** | Clauset-Newman-Moore Modularity $Q$ + Sticky Canary Routing + Shadow-Mode ACL | `python examples/showcases/brownfield_modernization_demo.py` |
| **3. Parallel DAG & DevX** | Concurrent Multi-Reviewer DAG + Privacy-Preserving SPACE & DORA Telemetry | `python examples/showcases/parallel_devx_demo.py` |

All three showcases are executed automatically in CI via `tests/unit/test_showcase_examples.py` so they never drift out of sync with the codebase.
