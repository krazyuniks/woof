# Deploy-aware merge coordinator proof

Work unit: `deploy-aware-merge-coordinator`.

## Red run

Command:

```text
uv run pytest tests/unit/test_merge_coordinator.py::test_per_pr_mark_done_reconciles_before_deploy_halt tests/unit/test_merge_coordinator.py::test_terminal_ci_waits_before_next_deploy_triggering_merge tests/unit/test_merge_coordinator.py::test_coordinator_waits_for_recomputed_checks_after_force_push_before_merge tests/unit/test_merge_coordinator.py::test_merge_body_closes_issue_and_records_artefact_lineage tests/unit/test_preflight.py::test_deploy_aware_profile_a_preflight_requires_terminal_deploy_checks
```

Result before implementation: red. Collection failed because `woof.graph.merge.CheckRunState` did not exist, proving the deploy-aware check-run seam and dependent behaviours were absent from the unfixed tree.

## Green targeted run

Command:

```text
uv run pytest tests/unit/test_merge_coordinator.py::test_profile_a_merge_policy_reads_deploy_aware_knobs tests/unit/test_merge_coordinator.py::test_per_pr_mark_done_reconciles_before_deploy_halt tests/unit/test_merge_coordinator.py::test_proved_terraform_state_lock_contention_halts_before_next_merge tests/unit/test_merge_coordinator.py::test_terminal_ci_waits_before_next_deploy_triggering_merge tests/unit/test_merge_coordinator.py::test_coordinator_waits_for_recomputed_checks_after_force_push_before_merge tests/unit/test_merge_coordinator.py::test_merge_body_closes_issue_and_records_artefact_lineage tests/unit/test_preflight.py::test_deploy_aware_profile_a_preflight_requires_terminal_deploy_checks
```

Result after implementation: green, 7 passed in 0.11s.
