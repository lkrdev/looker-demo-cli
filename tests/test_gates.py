"""Tests for :mod:`looker_demo_cli.gates`.

Scope: the pure gate model behind ``demo-create status`` -- which gates are
complete, which one is current, which ones pause for a human, and the literal
command each one emits.

Why these tests are unusually strict about strings
--------------------------------------------------
The command strings this module produces are **executed verbatim by an AI
orchestrator**. There is no human between the string and the shell. A wrong
flag name does not surface as a helpful error; it surfaces as an agent that
runs ``--project`` against a command expecting ``--looker-project``, reads the
usage error, and starts improvising. So the exact command for both an empty and
a fully populated state is pinned character for character, and any change to
one has to be a deliberate edit to a test.

Every test is hermetic by construction: the module under test performs no I/O,
so no fixture, fake, or temporary directory is required. States are built with
an explicit ``gcp_project_id`` wherever a command string is asserted, because
``FlowState`` defaults that field from ``$GOOGLE_CLOUD_PROJECT`` and a
developer with it set would otherwise see different output than CI.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from looker_demo_cli import gates as gates_module
from looker_demo_cli.gates import (
    COMMAND_PREFIX,
    GATES,
    completed_gates,
    current_gate,
    evaluate_gates,
    is_pipeline_complete,
)
from looker_demo_cli.state import FlowState

pytestmark = pytest.mark.unit


# ===========================================================================
# State builders
# ===========================================================================


def _unstarted_state(**overrides) -> FlowState:
    """A state in which nothing has run yet.

    ``gcp_project_id`` is forced blank rather than left to its default so the
    resulting command strings do not depend on the developer's environment.
    It is applied via ``setdefault`` so a caller can still pin a specific
    blank form (``""`` vs ``"   "``) without colliding on the keyword.

    Args:
        **overrides: Fields to set on top of the unstarted baseline.

    Returns:
        A ``FlowState`` with no gate signals present.
    """
    overrides.setdefault("gcp_project_id", "")
    return FlowState(**overrides)


def _finished_state(**overrides) -> FlowState:
    """A state in which every gate has run to completion.

    Args:
        **overrides: Fields to set on top of the finished baseline.

    Returns:
        A ``FlowState`` carrying every gate's completion signal.
    """
    fields = {
        "precheck_passed": True,
        "gcp_project_id": "acme-analytics",
        "gcp_account": "analyst@acme.com",
        "looker_account": "acme-looker",
        "looker_connection_name": "acme_bigquery",
        "dataset_exists": True,
        "bq_dataset_id": "retail",
        "domain_name": "retail",
        "generated_parquet_dir": Path("/scratch/retail"),
        "generated_tables": ["fct_orders", "dim_users"],
        "looker_project_name": "retail_demo",
        "lookml_model_name": "retail_demo",
        "lookml_output_dir": Path("/scratch/lookml_retail_demo"),
        "existing_tables": ["fct_orders", "dim_users"],
        "deployed_dashboard_url": "https://acme.looker.com/dashboards/42",
        "deployed_dashboard_id": "42",
        "ca_agent_id": "agent-42",
        "published_to_ge": True,
    }
    fields.update(overrides)
    return FlowState(**fields)


#: The single field each gate reads, and a value that satisfies it. Kept as one
#: table so a predicate change has exactly one place to be reflected.
_COMPLETION_SIGNAL: dict[str, dict[str, object]] = {
    "gate_0_environment": {"precheck_passed": True},
    "gate_1_data": {"dataset_exists": True},
    "gate_2_model": {"lookml_output_dir": Path("/scratch/lookml")},
    "gate_3_deploy": {"deployed_dashboard_url": "https://acme.looker.com/dashboards/42"},
    "gate_4_agent": {"ca_agent_id": "agent-42"},
    "gate_5_publish": {"published_to_ge": True},
}

_GATE_IDS: list[str] = [gate.id for gate in GATES]


def _state_satisfying(*gate_ids: str) -> FlowState:
    """Build a state carrying exactly the named gates' completion signals.

    Args:
        *gate_ids: Gate ids to satisfy.

    Returns:
        A ``FlowState`` with those gates complete and no others.
    """
    overrides: dict[str, object] = {}
    for gate_id in gate_ids:
        overrides.update(_COMPLETION_SIGNAL[gate_id])
    return _unstarted_state(**overrides)


# ===========================================================================
# Structural invariants
# ===========================================================================


def test_gates_are_numbered_contiguously_from_zero():
    """Gate numbers are an index, not a label.

    ``status`` reports ``gate`` numbers into ``next_actions``, and the rest of
    the CLI already emits ``gate=1``/``gate=3``/``gate=5`` from its own
    commands. A gap or a renumbering would silently desynchronise the two.
    """
    assert [gate.number for gate in GATES] == list(range(len(GATES)))


def test_gate_ids_are_unique():
    """Ids are the machine-readable key an orchestrator branches on.

    ``completed_gates`` in the JSON payload is a list of ids, and
    ``evaluate_gates`` marks the current gate by comparing ids. A duplicate
    would make both ambiguous.
    """
    assert len(set(_GATE_IDS)) == len(GATES)


def test_gate_count_matches_the_documented_workflow():
    """The workflow is defined as gates 0 through 5.

    Pinned because adding a seventh gate is a change to the published contract
    an agent drives, not an implementation detail.
    """
    assert len(GATES) == 6


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_gate_id_encodes_its_own_number(gate):
    """An id read in isolation must reveal its position.

    Log lines and JSON payloads carry the id without the number beside it; an
    id that disagreed with its number would make a report misleading rather
    than merely terse.
    """
    assert gate.id.startswith(f"gate_{gate.number}_")


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_human_checkpoint_is_present_exactly_when_confirmation_is_required(gate):
    """Telling an agent to ask the user, without saying what to ask, is not actionable.

    The orchestrator branches on ``requires_human_confirmation`` and then has
    to render a question; the checkpoint sentence *is* that question. Equally,
    a checkpoint on a gate that never pauses would be dead text that drifts.
    """
    assert (gate.human_checkpoint is not None) == gate.requires_human_confirmation
    if gate.human_checkpoint is not None:
        assert gate.human_checkpoint.strip()


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_gate_titles_are_non_empty(gate):
    """The title is the only thing shown in the Rich table's Stage column."""
    assert gate.title.strip()


def test_only_gate_two_runs_without_a_human_pause():
    """Pins which gates stop for a human, since that is the whole workflow.

    Gates 0, 1, 3, 4 and 5 each mandate an explicit confirmation. Gate 2's only
    branch -- whether a 3NF schema needs NDT rollups -- is decided from the
    schema by a subagent, so pausing there would ask the user a question they
    have no input on. Any change to this set is a workflow change.
    """
    non_pausing = [gate.id for gate in GATES if not gate.requires_human_confirmation]
    assert non_pausing == ["gate_2_model"]


def test_module_stays_free_of_io_and_presentation_dependencies():
    """The gate model is reused by tests, by ``status``, and later by others.

    The moment it imports ``typer`` or ``rich``, judging a state requires a CLI
    runner, and the cheap plain-dataclass tests below stop being possible.
    """
    forbidden = {"typer", "rich", "subprocess", "requests", "google", "os", "shutil", "io"}

    imported_roots = set()
    for line in inspect.getsource(gates_module).splitlines():
        if line.startswith("from "):
            imported_roots.add(line.split()[1].split(".")[0])
        elif line.startswith("import "):
            imported_roots.add(line.split()[1].split(".")[0])

    assert not (imported_roots & forbidden), f"gates must stay pure; found {imported_roots & forbidden}"


# ===========================================================================
# current_gate / completed_gates
# ===========================================================================


def test_unstarted_state_starts_at_gate_zero():
    """A fresh checkout must be told to run the environment audit first.

    This is the entry point of the whole feature: an agent with no memory runs
    ``status`` and is pointed at gate 0 rather than guessing.
    """
    assert current_gate(_unstarted_state()).id == "gate_0_environment"


def test_unstarted_state_has_no_completed_gates():
    """Nothing has run, so nothing may be reported as done."""
    assert completed_gates(_unstarted_state()) == []


def test_unstarted_state_is_not_complete():
    """``is_complete`` in the payload must be false before anything runs."""
    assert is_pipeline_complete(_unstarted_state()) is False


@pytest.mark.parametrize(
    ("satisfied", "expected_current"),
    [
        ("gate_0_environment", "gate_1_data"),
        ("gate_1_data", "gate_0_environment"),
        ("gate_2_model", "gate_0_environment"),
        ("gate_3_deploy", "gate_0_environment"),
        ("gate_4_agent", "gate_0_environment"),
        ("gate_5_publish", "gate_0_environment"),
    ],
    ids=[
        "gate_0_alone_advances_to_gate_1",
        "gate_1_alone_still_needs_gate_0",
        "gate_2_alone_still_needs_gate_0",
        "gate_3_alone_still_needs_gate_0",
        "gate_4_alone_still_needs_gate_0",
        "gate_5_alone_still_needs_gate_0",
    ],
)
def test_current_gate_is_strictly_the_first_incomplete_gate(satisfied, expected_current):
    """A later signal does not license skipping the gates before it.

    These states are reachable: a user can re-point the CLI at a new GCP
    project (clearing nothing) or hand-edit ``.demo-state.json``. The gates
    carry real data dependencies -- ``lookml model`` introspects the dataset
    gate 1 loads -- so "furthest signal wins" would send an agent to model a
    warehouse that does not exist. First-incomplete-wins is the safe reading.
    """
    assert current_gate(_state_satisfying(satisfied)).id == expected_current


@pytest.mark.parametrize(
    ("satisfied_count", "expected_current"),
    [
        (0, "gate_0_environment"),
        (1, "gate_1_data"),
        (2, "gate_2_model"),
        (3, "gate_3_deploy"),
        (4, "gate_4_agent"),
        (5, "gate_5_publish"),
        (6, None),
    ],
    ids=[
        "nothing_done",
        "through_gate_0",
        "through_gate_1",
        "through_gate_2",
        "through_gate_3",
        "through_gate_4",
        "everything_done",
    ],
)
def test_current_gate_walks_forward_as_each_prefix_completes(satisfied_count, expected_current):
    """The happy path: each finished gate hands off to exactly the next one.

    This is the loop an orchestrator runs -- call ``status``, run
    ``next_command``, call ``status`` again -- so it must terminate and must not
    stall on or repeat a gate.
    """
    state = _state_satisfying(*_GATE_IDS[:satisfied_count])
    current = current_gate(state)
    assert (current.id if current else None) == expected_current


def test_completed_gates_reports_out_of_order_signals():
    """An inconsistent state must be legible, not silently normalised.

    ``current_gate`` deliberately ignores the stray gate 5 signal, but hiding
    it from ``completed_gates`` too would leave a user staring at a published
    agent the report claims does not exist.
    """
    state = _state_satisfying("gate_5_publish")
    assert [gate.id for gate in completed_gates(state)] == ["gate_5_publish"]


def test_completed_gates_preserves_pipeline_order():
    """The report is read top to bottom; scrambling it would be a bug."""
    state = _state_satisfying("gate_3_deploy", "gate_0_environment", "gate_1_data")
    assert [gate.id for gate in completed_gates(state)] == [
        "gate_0_environment",
        "gate_1_data",
        "gate_3_deploy",
    ]


def test_finished_state_has_no_current_gate():
    """``None`` is how the payload says "the build is done".

    ``status`` maps this straight onto ``next_command: null``, which is the
    orchestrator's stop condition.
    """
    assert current_gate(_finished_state()) is None


def test_finished_state_is_complete():
    """The positive form of the stop condition, read by ``is_complete``."""
    assert is_pipeline_complete(_finished_state()) is True


def test_finished_state_completes_every_gate():
    """Guards the fully populated fixture itself.

    If a completion predicate later reads a different field, this fails loudly
    rather than quietly weakening every test built on ``_finished_state``.
    """
    assert [gate.id for gate in completed_gates(_finished_state())] == _GATE_IDS


# ===========================================================================
# evaluate_gates
# ===========================================================================


def test_evaluate_gates_reports_every_gate_in_order():
    """The Rich table and the JSON array are both rendered straight from this."""
    statuses = evaluate_gates(_unstarted_state())
    assert [s.gate.id for s in statuses] == _GATE_IDS


def test_evaluate_gates_marks_exactly_one_current_gate():
    """Two "current" rows would make the table meaningless and the payload ambiguous."""
    statuses = evaluate_gates(_state_satisfying("gate_0_environment", "gate_1_data"))
    current = [s.gate.id for s in statuses if s.is_current]
    assert current == ["gate_2_model"]


def test_evaluate_gates_marks_no_current_gate_when_finished():
    """Every row is complete, so nothing may be highlighted as next to do."""
    statuses = evaluate_gates(_finished_state())
    assert not any(s.is_current for s in statuses)
    assert all(s.complete for s in statuses)


def test_evaluate_gates_agrees_with_the_individual_predicates():
    """One pass over the gates must not drift from asking each gate directly."""
    state = _state_satisfying("gate_0_environment", "gate_2_model")
    statuses = evaluate_gates(state)
    assert {s.gate.id: s.complete for s in statuses} == {gate.id: gate.is_complete(state) for gate in GATES}


def test_evaluate_gates_supplies_a_command_for_completed_gates_too():
    """A finished gate is still re-runnable, and re-running needs its arguments.

    Re-deploying after a hand edit is routine; making the caller reconstruct
    the flags from scratch is exactly the drift this feature removes.
    """
    statuses = {s.gate.id: s for s in evaluate_gates(_finished_state())}
    assert statuses["gate_3_deploy"].command.startswith(COMMAND_PREFIX)


def test_gate_status_to_dict_carries_the_published_keys():
    """These key names are the JSON contract an orchestrator parses."""
    status = evaluate_gates(_unstarted_state())[0]
    assert set(status.to_dict()) == {
        "number",
        "id",
        "title",
        "complete",
        "is_current",
        "command",
        "requires_human_confirmation",
        "human_checkpoint",
    }


def test_gate_describe_omits_state_dependent_fields():
    """``describe`` is the state-independent half, reused wherever no state exists."""
    assert set(GATES[0].describe()) == {
        "number",
        "id",
        "title",
        "requires_human_confirmation",
        "human_checkpoint",
    }


# ===========================================================================
# Command generation -- general invariants
# ===========================================================================


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
@pytest.mark.parametrize(
    "state_builder",
    [_unstarted_state, _finished_state],
    ids=["unstarted_state", "finished_state"],
)
def test_every_command_is_a_demo_create_invocation(gate, state_builder):
    """An agent pipes this straight into a shell; it must be our CLI.

    Checked for both an empty and a full state because the interpolation paths
    differ, and a placeholder branch that emitted bare arguments would only
    break in the case nobody demos.
    """
    assert gate.command(state_builder()).startswith(COMMAND_PREFIX)


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
@pytest.mark.parametrize(
    "state_builder",
    [_unstarted_state, _finished_state],
    ids=["unstarted_state", "finished_state"],
)
def test_no_command_contains_unresolved_format_braces(gate, state_builder):
    """A stray ``{`` means an f-string or template failed to render.

    The failure mode is silent: the agent runs ``--dataset {dataset}``, BigQuery
    is asked for a table that cannot exist, and the error surfaces three steps
    downstream. Angle-bracketed placeholders are the only permitted "unknown".
    """
    command = gate.command(state_builder())
    assert "{" not in command and "}" not in command


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_placeholders_are_balanced_angle_brackets(gate):
    """A half-written placeholder reads as a shell redirect, not as a prompt.

    ``--dataset <dataset-id`` would be executed; ``--dataset <dataset-id>`` is
    obviously a value the agent must supply.
    """
    command = gate.command(_unstarted_state())
    assert command.count("<") == command.count(">")


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_finished_state_leaves_no_placeholders(gate):
    """Once everything is known, nothing may still be asked of the caller.

    A placeholder surviving into a fully populated state means a value the
    state records is not being read -- the exact drift this module exists to
    prevent.
    """
    assert "<" not in gate.command(_finished_state())


# ===========================================================================
# Command generation -- exact strings
# ===========================================================================

_UNSTARTED_COMMANDS: dict[str, str] = {
    "gate_0_environment": "demo-create pre-check --fix --gcp-project <gcp-project>",
    "gate_1_data": "demo-create data generate --domain <domain> --row-count <row-count> --output-dir <output-dir>",
    # Every identifier here is a placeholder: as of 0.3.0 `FlowState` no longer
    # seeds `bq_dataset_id` / `looker_project_name` / `lookml_model_name` /
    # `looker_connection_name` with the "logistics_analytics" placeholder
    # literals that used to make an unstarted build look half-configured.
    "gate_2_model": (
        "demo-create lookml model --looker-project <looker-project> --dataset <dataset-id> "
        "--connection <connection-name> --gcp-project <gcp-project>"
    ),
    "gate_3_deploy": "demo-create lookml deploy --looker-project <looker-project> --lookml-dir <lookml-dir>",
    "gate_4_agent": "demo-create agent create --model <model-name> --explore <explore-name>",
    "gate_5_publish": "demo-create agent publish --agent-id <agent-id>",
}

_FINISHED_COMMANDS: dict[str, str] = {
    "gate_0_environment": "demo-create pre-check --fix --gcp-project acme-analytics",
    "gate_1_data": (
        "demo-create data upload --parquet-dir /scratch/retail --gcp-project acme-analytics --dataset retail"
    ),
    "gate_2_model": (
        "demo-create lookml model --looker-project retail_demo --dataset retail "
        "--connection acme_bigquery --gcp-project acme-analytics"
    ),
    "gate_3_deploy": (
        "demo-create lookml deploy --looker-project retail_demo "
        "--lookml-dir /scratch/lookml_retail_demo --looker-account acme-looker"
    ),
    "gate_4_agent": (
        "demo-create agent create --model retail_demo --explore fct_orders "
        "--dashboards-dir /scratch/lookml_retail_demo/dashboards"
    ),
    "gate_5_publish": "demo-create agent publish --agent-id agent-42",
}


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_unstarted_state_command_is_exactly_as_published(gate):
    """Pins the flag names an agent will type before any state exists.

    Every flag here was taken from the target command's real ``typer``
    signature: ``--looker-project`` (not ``--project``, which would collide
    with ``--gcp-project``), ``--connection``, ``--lookml-dir``,
    ``--parquet-dir``, ``--output-dir``, ``--row-count``. If one of those
    commands renames a flag, this test is the tripwire.
    """
    assert gate.command(_unstarted_state()) == _UNSTARTED_COMMANDS[gate.id]


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_finished_state_command_is_exactly_as_published(gate):
    """Pins that recorded values are substituted, not merely available.

    The point of the feature is that an agent never re-derives an argument: if
    state says the dataset is ``retail``, the command must already say
    ``--dataset retail``.
    """
    assert gate.command(_finished_state()) == _FINISHED_COMMANDS[gate.id]


# ===========================================================================
# Command generation -- branch behavior
# ===========================================================================


def test_gate_one_asks_for_upload_once_parquet_exists():
    """Re-running ``data generate`` would throw away a reviewed dataset.

    Gate 1 spans two commands. Once Phase 2 has produced Parquet the user has
    inspected, the only remaining work is the load, and regenerating would
    resample every distribution they just approved.
    """
    state = _unstarted_state(generated_parquet_dir=Path("/scratch/retail"), bq_dataset_id="retail")
    assert GATES[1].command(state).startswith("demo-create data upload ")


def test_gate_one_asks_for_generate_before_any_parquet_exists():
    """With nothing on disk there is nothing to upload."""
    assert GATES[1].command(_unstarted_state()).startswith("demo-create data generate ")


def test_gate_one_never_guesses_the_row_count():
    """Row volume is the user's Phase 3 decision, not the CLI's.

    Substituting a remembered value would let an agent load 500k rows because a
    previous demo did, silently bypassing the confirmation the gate exists for.
    """
    assert "--row-count <row-count>" in GATES[1].command(_finished_state(generated_parquet_dir=None))


def test_gate_three_omits_the_looker_account_flag_when_none_is_recorded():
    """An invented OAuth alias fails harder than an omitted optional flag.

    With the flag absent, ``lookml deploy`` falls back to the saved ``lkr``
    session, which is right far more often than any alias an agent could guess.
    """
    state = _finished_state(looker_account=None)
    assert "--looker-account" not in GATES[3].command(state)


def test_gate_three_passes_through_a_recorded_looker_account():
    """When the account *is* known, omitting it would risk the wrong instance."""
    state = _finished_state(looker_account="acme-looker")
    assert GATES[3].command(state).endswith("--looker-account acme-looker")


def test_gate_four_falls_back_from_generated_tables_to_modelled_tables():
    """A dataset modelled from pre-existing BigQuery tables has no generated ones.

    ``existing_tables`` is what ``lookml model`` records in that path; without
    the fallback, the whole "bring your own warehouse" flow would emit an
    ``<explore-name>`` placeholder even though the explore is known.
    """
    state = _finished_state(generated_tables=[], existing_tables=["fct_shipments"])
    assert "--explore fct_shipments" in GATES[4].command(state)


def test_gate_four_leaves_the_explore_unresolved_when_no_tables_are_known():
    """Better an obvious blank than a confidently wrong explore name."""
    state = _unstarted_state(generated_tables=[], existing_tables=[])
    assert "--explore <explore-name>" in GATES[4].command(state)


def test_gate_four_points_at_the_dashboards_subdirectory():
    """Golden queries are mined from the dashboard files, which live one level down.

    ``agent create`` accepts either the LookML root or its ``dashboards/``
    child; naming the precise directory keeps grounding from depending on how
    the generator happens to lay the tree out.
    """
    state = _finished_state(lookml_output_dir=Path("/scratch/lookml_retail_demo"))
    assert GATES[4].command(state).endswith("--dashboards-dir /scratch/lookml_retail_demo/dashboards")


def test_gate_four_omits_the_dashboards_flag_before_lookml_is_generated():
    """Pointing at a directory that does not exist yet is worse than saying nothing."""
    assert "--dashboards-dir" not in GATES[4].command(_unstarted_state())


@pytest.mark.parametrize(
    "blank",
    ["", "   "],
    ids=["empty_string", "whitespace_only"],
)
def test_blank_state_values_render_as_placeholders(blank):
    """``FlowState`` defaults ``gcp_project_id`` from the environment.

    On a machine with no ``$GOOGLE_CLOUD_PROJECT`` that default is ``""``.
    Interpolating it produces ``--gcp-project`` followed by the next flag,
    which the CLI parses as the project *being* ``--json``. A blank must be
    treated as unknown.
    """
    assert "--gcp-project <gcp-project>" in GATES[0].command(_unstarted_state(gcp_project_id=blank))


def test_default_flow_state_is_judged_without_error():
    """``status`` runs against a brand-new ``FlowState`` on a fresh machine.

    Constructed with no arguments at all, i.e. with whatever the environment
    supplies, so an environment-derived default cannot crash the one command an
    agent calls first.
    """
    statuses = evaluate_gates(FlowState())
    assert len(statuses) == len(GATES)
    assert all(status.command.startswith(COMMAND_PREFIX) for status in statuses)
