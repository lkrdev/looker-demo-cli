"""Tests for ``demo-create status``.

Scope: the I/O shell around the pure gate model -- the JSON envelope an
orchestrator parses, the Rich table a human reads, ``--state-file`` redirection,
and the failure path when the state file on disk is unusable.

Why this command is tested harder than its size suggests
--------------------------------------------------------
``status`` is the command an AI agent calls *most*, and the only one whose
entire purpose is to be machine-read. Everything else in the CLI can afford a
cosmetic regression; here, a Rich banner leaking onto stdout breaks
``json.loads`` and takes the whole self-describing workflow with it. So the
stream split is asserted directly rather than inferred: ``result.stdout`` and
``result.stderr`` separately, never ``result.output``, which mixes them and
would pass whichever stream the text arrived on.

The second contract under test is that the agent's loop terminates and does not
drift: ``next_actions[0].command`` must equal ``data.next_command`` (two fields,
one truth), and a finished pipeline must produce *no* next action at all.

Every test is hermetic. ``status`` performs exactly one side effect -- reading a
file -- so the only fixtures needed are ``isolated_cwd`` (autouse) and
``state_file``; no Looker, BigQuery, or shell fake is required, and their
absence is itself a guarantee that this command never reaches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from looker_demo_cli.errors import StateError
from looker_demo_cli.gates import GATES
from looker_demo_cli.output import ENVELOPE_SCHEMA_VERSION, set_json_mode
from looker_demo_cli.state import STATE_FILE_NAME, FlowState

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Local fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_json_mode():
    """Keep the ``--json`` ContextVar from leaking between tests.

    ``output.set_json_mode`` writes to a ``ContextVar`` that ``CliRunner``
    shares with the test process. A ``--json`` invocation here would otherwise
    silently flip the rendering mode of a later test that never passed the flag
    -- and half of this module asserts on which stream received what.
    """
    set_json_mode(False)
    yield
    set_json_mode(False)


#: Characters Rich draws a table with. Their presence on stdout is the precise
#: regression this command cannot survive, so they are checked for by identity
#: rather than by parsing the output.
_BOX_DRAWING_CHARS = "─│┌┐└┘━┃┏┓┗┛┡┩╇┳┻├┤"


def envelope(result: Any) -> dict[str, Any]:
    """Parse the JSON envelope a ``--json`` invocation wrote to stdout.

    Deliberately parses ``result.stdout`` rather than ``result.output``: the
    contract is that stdout carries the envelope and nothing else, so a bare
    ``json.loads`` succeeding is itself an assertion that nothing leaked.
    """
    return json.loads(result.stdout)


def payload(result: Any) -> dict[str, Any]:
    """Return the ``status`` report, which lives under ``data``."""
    return envelope(result)["data"]


#: Identity values every command string interpolates. Set explicitly rather
#: than left to defaults so the pinned command strings below cannot change
#: because of a developer's environment.
#:
#: ``looker_account`` is deliberately absent: gate 3 appends
#: ``--looker-account`` only when one is recorded, and leaving it out keeps the
#: expected strings deterministic while a separate test covers the append.
_IDENTITY: dict[str, Any] = {
    "gcp_project_id": "acme-analytics",
    "bq_dataset_id": "retail",
    "domain_name": "retail",
    "looker_connection_name": "acme_bigquery",
    "looker_project_name": "retail_demo",
    "lookml_model_name": "retail_demo",
    "generated_tables": ["fct_orders"],
}

#: The single field each gate's completion is read from, in pipeline order.
_GATE_SIGNALS: list[tuple[str, Any]] = [
    ("precheck_passed", True),
    ("dataset_exists", True),
    ("lookml_output_dir", "/scratch/lookml_retail_demo"),
    ("deployed_dashboard_url", "https://acme.looker.com/dashboards/42"),
    ("ca_agent_id", "agent-42"),
    ("published_to_ge", True),
]


def _state_through(step: int) -> dict[str, Any]:
    """Build the state fields for a run that has completed ``step`` gates.

    Args:
        step: How many gates, counting from 0, have been completed.

    Returns:
        Keyword arguments for the ``state_file`` fixture.
    """
    fields = dict(_IDENTITY)
    for name, value in _GATE_SIGNALS[:step]:
        fields[name] = value
    return fields


#: ``(gates completed, expected current gate id, expected next command)``.
#:
#: These command strings are what an agent executes verbatim, so they are
#: pinned in full rather than matched by prefix.
_WALK: list[tuple[int, str | None, str | None]] = [
    (0, "gate_0_environment", "demo-create pre-check --fix --gcp-project acme-analytics"),
    (
        1,
        "gate_1_data",
        "demo-create data generate --domain retail --row-count <row-count> --output-dir <output-dir>",
    ),
    (
        2,
        "gate_2_model",
        "demo-create lookml model --looker-project retail_demo --dataset retail "
        "--connection acme_bigquery --gcp-project acme-analytics",
    ),
    (
        3,
        "gate_3_deploy",
        "demo-create lookml deploy --looker-project retail_demo --lookml-dir /scratch/lookml_retail_demo",
    ),
    (
        4,
        "gate_4_agent",
        "demo-create agent create --model retail_demo --explore fct_orders "
        "--dashboards-dir /scratch/lookml_retail_demo/dashboards",
    ),
    (5, "gate_5_publish", "demo-create agent publish --agent-id agent-42"),
    (6, None, None),
]

_WALK_IDS = [
    "nothing_done",
    "through_gate_0",
    "through_gate_1",
    "through_gate_2",
    "through_gate_3",
    "through_gate_4",
    "everything_done",
]


# ===========================================================================
# The output contract: stdout carries JSON, stderr carries Rich
# ===========================================================================


def test_json_mode_stdout_parses_as_json(invoke) -> None:
    """The one thing an orchestrator does with this command is parse its stdout.

    Asserted against a *fresh* state, which is the very first call an agent
    makes: if the empty case emitted a banner ahead of the payload, the feature
    would fail before it ever produced a useful answer.
    """
    result = invoke(["status", "--json"])

    assert result.exit_code == 0
    assert result.stdout.lstrip().startswith("{")
    assert json.loads(result.stdout)["command"] == "status"


def test_json_mode_stdout_contains_no_rich_output(invoke, state_file) -> None:
    """``emit`` is either/or, never a tee.

    A table rendered *in addition to* the envelope still breaks ``jq``: the box
    characters are not valid JSON. Checking for the glyphs directly catches a
    tee even in the case where the JSON happens to be printed first and a
    lenient parser would have stopped at the closing brace.
    """
    state_file(**_state_through(3))

    result = invoke(["status", "--json"])

    leaked = [char for char in _BOX_DRAWING_CHARS if char in result.stdout]
    assert not leaked, f"Rich box characters leaked onto stdout: {leaked}"


def test_json_mode_writes_nothing_to_stderr(invoke) -> None:
    """The converse of the split: JSON mode is silent, not merely redirected.

    An agent that captures stderr for diagnostics should see an empty stream on
    success, so anything appearing there is a genuine signal rather than
    routine narration it has to learn to ignore.
    """
    result = invoke(["status", "--json"])

    assert result.stderr == ""


def test_human_mode_writes_nothing_to_stdout(invoke, state_file) -> None:
    """A human-mode run piped into ``jq`` must yield an empty stream, not garbage.

    This is the failure the output contract was introduced to fix: Rich on
    stdout meant every ``--json``-less invocation produced a parse error rather
    than nothing at all.
    """
    state_file(**_state_through(2))

    result = invoke(["status"])

    assert result.exit_code == 0
    assert result.stdout == ""


def test_human_mode_renders_the_gate_table_on_stderr(invoke, state_file) -> None:
    """The human-readable half must actually be produced, not merely relocated.

    Pinning every gate title guards the case where the table renders but the
    rows are silently dropped -- an empty table is worse than no table, because
    it reads as "no gates" rather than as a bug.
    """
    state_file(**_state_through(2))

    result = invoke(["status"])

    assert any(char in result.stderr for char in _BOX_DRAWING_CHARS)
    for gate in GATES:
        assert gate.title in result.stderr


def test_human_mode_distinguishes_complete_current_and_pending(invoke, state_file) -> None:
    """The table's only job is to show *where the build is*.

    With two gates done, a reader must be able to see three distinct states at
    a glance; a rendering that collapsed them would be strictly less useful
    than reading the raw JSON.
    """
    state_file(**_state_through(2))

    result = invoke(["status"])

    assert result.stderr.count("complete") >= 2
    assert "current" in result.stderr
    assert "pending" in result.stderr


def test_human_mode_prints_the_next_command_on_stderr(invoke, state_file) -> None:
    """A human reading the table still needs the literal command to copy.

    It is printed by ``emit`` from ``next_actions`` rather than by the
    renderer, so this also pins that the two halves of the human output stay
    wired together.
    """
    state_file(**_state_through(3))

    result = invoke(["status"])

    assert "demo-create lookml deploy --looker-project retail_demo" in result.stderr


def test_human_mode_announces_a_finished_pipeline(invoke, state_file) -> None:
    """Having nothing left to do must be stated, not inferred from a missing command.

    A table of six ticks and no closing line reads like output that got cut
    off.
    """
    state_file(**_state_through(6))

    result = invoke(["status"])

    assert "All gates complete" in result.stderr


# ===========================================================================
# A fresh machine
# ===========================================================================


def test_fresh_state_exits_zero(invoke) -> None:
    """Having run nothing yet is not an error.

    ``status`` is the first command an agent calls, before any state exists. A
    non-zero exit here would read as a broken installation and stop the run at
    the point it was supposed to start.
    """
    result = invoke(["status", "--json"])

    assert result.exit_code == 0


def test_fresh_state_starts_at_gate_zero(invoke) -> None:
    """The entry point of the whole feature: an agent with no memory is oriented."""
    data = payload(invoke(["status", "--json"]))

    assert data["current_gate"]["id"] == "gate_0_environment"
    assert data["current_gate"]["number"] == 0


def test_fresh_state_reports_nothing_complete(invoke) -> None:
    """Nothing has run, so nothing may be claimed as done."""
    data = payload(invoke(["status", "--json"]))

    assert data["completed_gates"] == []
    assert data["is_complete"] is False


def test_status_creates_no_state_file(invoke, isolated_cwd: Path) -> None:
    """``status`` is read-only, and being safe to call between gates depends on it.

    If it wrote a file, an agent polling for orientation would materialise a
    default state -- and the next command would inherit values nobody chose.
    """
    invoke(["status", "--json"])

    assert not (isolated_cwd / STATE_FILE_NAME).exists()


def test_status_does_not_rewrite_an_existing_state_file(invoke, state_file) -> None:
    """Even a byte-identical rewrite would be wrong.

    Round-tripping through ``FlowState`` silently normalises the file --
    injecting defaults for absent keys. A later command would then read values
    that ``status`` invented rather than ones a gate recorded.
    """
    path = state_file(**_state_through(3))
    before = path.read_bytes()

    invoke(["status", "--json"])

    assert path.read_bytes() == before


# ===========================================================================
# The 0 -> 5 walk
# ===========================================================================


@pytest.mark.parametrize(("completed", "expected_gate_id", "_command"), _WALK, ids=_WALK_IDS)
def test_walk_reports_the_expected_current_gate(invoke, state_file, completed, expected_gate_id, _command) -> None:
    """The orchestrator's loop: call status, run the command, call status again.

    Each finished gate must hand off to exactly the next one. A step that
    stalled would loop forever; one that skipped ahead would build a model on a
    dataset that was never loaded.
    """
    state_file(**_state_through(completed))

    data = payload(invoke(["status", "--json"]))
    current = data["current_gate"]

    assert (current["id"] if current else None) == expected_gate_id


@pytest.mark.parametrize(("completed", "_gate_id", "expected_command"), _WALK, ids=_WALK_IDS)
def test_walk_reports_the_expected_next_command(invoke, state_file, completed, _gate_id, expected_command) -> None:
    """The literal string an agent executes, at every point in the build.

    Pinned character for character because there is no human between this
    string and the shell: a renamed flag surfaces as an agent improvising
    against a usage error, not as a helpful failure.
    """
    state_file(**_state_through(completed))

    assert payload(invoke(["status", "--json"]))["next_command"] == expected_command


@pytest.mark.parametrize(("completed", "_gate_id", "_command"), _WALK, ids=_WALK_IDS)
def test_walk_accumulates_completed_gates(invoke, state_file, completed, _gate_id, _command) -> None:
    """Progress must be reported as a growing prefix, in pipeline order.

    An agent resuming a half-finished build reads this list to decide what it
    can rely on already existing.
    """
    state_file(**_state_through(completed))

    data = payload(invoke(["status", "--json"]))

    assert data["completed_gates"] == [gate.id for gate in GATES[:completed]]


@pytest.mark.parametrize(("completed", "_gate_id", "_command"), _WALK, ids=_WALK_IDS)
def test_walk_always_exits_zero(invoke, state_file, completed, _gate_id, _command) -> None:
    """Reporting progress never fails, at any point in the build.

    An agent that chains on ``&&`` would otherwise stop mid-pipeline because
    the *reporting* command, not the work, returned non-zero.
    """
    state_file(**_state_through(completed))

    assert invoke(["status", "--json"]).exit_code == 0


# ===========================================================================
# Termination
# ===========================================================================


def test_finished_pipeline_reports_completion(invoke, state_file) -> None:
    """``is_complete`` is the flag an agent stops on."""
    state_file(**_state_through(6))

    data = payload(invoke(["status", "--json"]))

    assert data["is_complete"] is True
    assert data["completed_gates"] == [gate.id for gate in GATES]


def test_finished_pipeline_nulls_the_current_gate_and_command(invoke, state_file) -> None:
    """Null, not an empty string or a repeat of the last command.

    An agent that tested truthiness on ``next_command`` and got ``""`` would
    stop correctly; one that got the gate 5 command again would republish
    forever.
    """
    state_file(**_state_through(6))

    data = payload(invoke(["status", "--json"]))

    assert data["current_gate"] is None
    assert data["next_command"] is None


def test_finished_pipeline_emits_no_next_actions(invoke, state_file) -> None:
    """An agent looping on ``next_actions`` must terminate.

    ``next_actions`` is the field the rest of the CLI already uses to chain
    steps, so it -- not just ``next_command`` -- has to run dry at the end.
    """
    state_file(**_state_through(6))

    assert envelope(invoke(["status", "--json"]))["next_actions"] == []


def test_finished_pipeline_requires_no_confirmation(invoke, state_file) -> None:
    """With no gate pending there is nothing to ask a human about.

    The field stays a plain ``false`` rather than becoming ``null``, so an
    orchestrator's ``if requires_human_confirmation`` branch does not need a
    special case for the finished build.
    """
    state_file(**_state_through(6))

    data = payload(invoke(["status", "--json"]))

    assert data["requires_human_confirmation"] is False
    assert data["human_checkpoint"] is None


# ===========================================================================
# next_actions consistency
# ===========================================================================


def test_next_action_command_matches_next_command(invoke, state_file) -> None:
    """Two fields, one truth.

    ``next_command`` is the convenient top-level field; ``next_actions`` is the
    convention every other command already emits. An agent may read either, so
    they must never disagree.
    """
    state_file(**_state_through(2))

    result = invoke(["status", "--json"])
    body = envelope(result)

    assert body["next_actions"][0]["command"] == body["data"]["next_command"]


def test_next_action_mirrors_the_current_gate(invoke, state_file) -> None:
    """The gate number and confirmation flag must agree across both fields too.

    An orchestrator that branches on ``next_actions[0]`` rather than on
    ``data`` must reach the same decision about pausing for a human.
    """
    state_file(**_state_through(3))

    body = envelope(invoke(["status", "--json"]))
    action = body["next_actions"][0]
    current = body["data"]["current_gate"]

    assert action["gate"] == current["number"]
    assert action["requires_human_confirmation"] == body["data"]["requires_human_confirmation"]


def test_exactly_one_next_action_is_emitted(invoke, state_file) -> None:
    """One current gate means one instruction.

    A second entry would force an agent to decide which to run first -- a
    choice this command exists to make on its behalf.
    """
    state_file(**_state_through(1))

    assert len(envelope(invoke(["status", "--json"]))["next_actions"]) == 1


# ===========================================================================
# The human-confirmation contract
# ===========================================================================


@pytest.mark.parametrize("completed", list(range(6)), ids=[gate.id for gate in GATES])
def test_confirmation_flag_matches_the_current_gate_policy(invoke, state_file, completed) -> None:
    """This single boolean is what an orchestrator branches on to call ``ask_question``.

    Checked at every gate, including gate 2 -- the only one that does *not*
    pause. A blanket ``true`` would still pass a test that only ever looked at
    gate 0, and would make the agent interrogate the user about a decision they
    have no input on.
    """
    state_file(**_state_through(completed))

    data = payload(invoke(["status", "--json"]))

    assert data["requires_human_confirmation"] is GATES[completed].requires_human_confirmation


@pytest.mark.parametrize("completed", list(range(6)), ids=[gate.id for gate in GATES])
def test_human_checkpoint_accompanies_every_confirmation(invoke, state_file, completed) -> None:
    """Being told to ask, without being told what to ask, is not actionable.

    The checkpoint sentence is the question text the agent renders, so it must
    be present exactly when the flag is set and absent when it is not.
    """
    state_file(**_state_through(completed))

    data = payload(invoke(["status", "--json"]))

    assert (data["human_checkpoint"] is not None) == data["requires_human_confirmation"]


# ===========================================================================
# The gates array
# ===========================================================================


def test_gates_array_lists_every_gate_in_order(invoke, state_file) -> None:
    """The array is the whole map, not only the part that remains.

    An agent rendering a progress view for the user reads this rather than
    reconstructing the pipeline from prose.
    """
    state_file(**_state_through(2))

    data = payload(invoke(["status", "--json"]))

    assert [entry["id"] for entry in data["gates"]] == [gate.id for gate in GATES]


def test_gates_array_entries_carry_the_published_keys(invoke) -> None:
    """These key names are the parsed contract; renaming one is a breaking change."""
    data = payload(invoke(["status", "--json"]))

    for entry in data["gates"]:
        assert {"number", "id", "title", "complete", "requires_human_confirmation", "human_checkpoint"} <= set(entry)


def test_gates_array_marks_exactly_one_gate_current(invoke, state_file) -> None:
    """Two highlighted rows would make the report ambiguous in both renderings."""
    state_file(**_state_through(4))

    data = payload(invoke(["status", "--json"]))

    assert [entry["id"] for entry in data["gates"] if entry["is_current"]] == ["gate_4_agent"]


# ===========================================================================
# --state-file
# ===========================================================================


def test_state_file_option_redirects_the_read(invoke, tmp_path: Path) -> None:
    """Two agents building different demos from one directory must not collide.

    Without this, both share the discovered ``.demo-state.json`` and each reads
    the other's progress -- which shows up as a build that mysteriously skips a
    gate it never ran.
    """
    early = tmp_path / "early.json"
    late = tmp_path / "late.json"
    early.write_text(FlowState(**_state_through(1)).model_dump_json(), encoding="utf-8")
    late.write_text(FlowState(**_state_through(4)).model_dump_json(), encoding="utf-8")

    early_data = payload(invoke(["status", "--json", "--state-file", str(early)]))
    late_data = payload(invoke(["status", "--json", "--state-file", str(late)]))

    assert early_data["current_gate"]["id"] == "gate_1_data"
    assert late_data["current_gate"]["id"] == "gate_4_agent"


def test_state_file_option_is_echoed_in_the_payload(invoke, tmp_path: Path) -> None:
    """The caller must be able to see *which* state produced this answer.

    The path is otherwise discovered from the working directory, so two
    invocations that legitimately disagree are indistinguishable without it.
    """
    explicit = tmp_path / "explicit.json"
    explicit.write_text(FlowState(**_state_through(2)).model_dump_json(), encoding="utf-8")

    data = payload(invoke(["status", "--json", "--state-file", str(explicit)]))

    assert Path(data["state_file"]) == explicit


def test_discovered_state_file_is_reported(invoke, state_file, isolated_cwd: Path) -> None:
    """With no flag, the reported path must be the one actually read.

    Reporting ``null`` -- which the discovery path would naturally produce --
    would leave an agent unable to tell the user which file to delete when the
    state is wrong.
    """
    state_file(**_state_through(1))

    data = payload(invoke(["status", "--json"]))

    assert Path(data["state_file"]).resolve() == (isolated_cwd / STATE_FILE_NAME).resolve()


def test_missing_explicit_state_file_is_a_fresh_start(invoke, tmp_path: Path) -> None:
    """A path that does not exist yet means "nothing has run", not an error.

    This is how an agent opens a *new* demo alongside an existing one: it names
    a state file before anything has written it.
    """
    absent = tmp_path / "not-created-yet.json"

    result = invoke(["status", "--json", "--state-file", str(absent)])
    data = payload(result)

    assert result.exit_code == 0
    assert data["current_gate"]["id"] == "gate_0_environment"
    assert Path(data["state_file"]) == absent


# ===========================================================================
# Unusable state files
# ===========================================================================


def test_corrupt_state_file_returns_a_structured_error(invoke, tmp_path: Path) -> None:
    """A truncated write must be reported, never silently treated as a fresh start.

    Concurrent gated commands can leave half-written JSON. Swallowing it would
    make a corrupt file indistinguishable from an empty one, and the run would
    continue against default values nobody chose -- against the wrong GCP
    project, in the worst case.
    """
    broken = tmp_path / "broken.json"
    broken.write_text('{"precheck_passed": tru', encoding="utf-8")

    result = invoke(["status", "--json", "--state-file", str(broken)])
    body = envelope(result)

    assert result.exit_code == StateError.exit_code
    assert body["status"] == "FAILED"
    assert body["command"] == "status"
    assert body["errors"][0]["code"] == "STATE_ERROR"
    assert body["errors"][0]["message"] == f"State file is not valid JSON: {broken}"
    assert body["errors"][0]["remediation"] == f"Delete {broken} to start from a clean state."


def test_corrupt_state_file_does_not_crash(invoke, tmp_path: Path) -> None:
    """The failure must arrive as an envelope and an exit code, not a traceback.

    An agent can act on ``STATE_ERROR`` plus a remediation string; it cannot
    act on a ``JSONDecodeError`` stack trace, and a crash would also mean the
    envelope never reached stdout.
    """
    broken = tmp_path / "broken.json"
    broken.write_text("not json at all", encoding="utf-8")

    result = invoke(["status", "--json", "--state-file", str(broken)])

    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.exit_code == StateError.exit_code


def test_corrupt_state_file_reports_on_stderr_in_human_mode(invoke, tmp_path: Path) -> None:
    """The stream split holds on the failure path too.

    A failure rendered to stdout would be the same tee regression as a success
    rendered there, except harder to notice: the caller is already handling a
    non-zero exit and may not look at what it parsed.
    """
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")

    result = invoke(["status", "--state-file", str(broken)])

    assert result.exit_code == StateError.exit_code
    assert result.stdout == ""
    assert "State file is not valid JSON" in result.stderr


def test_state_file_from_a_newer_cli_is_rejected(invoke, tmp_path: Path) -> None:
    """A partially-understood state file is more dangerous than none at all.

    A file written by a newer CLI may spell fields differently; loading it
    leniently would produce a report that looks right and points at the wrong
    project. Refusing is the safe reading, and it must surface through the same
    envelope as any other state failure.
    """
    future = tmp_path / "future.json"
    future.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    result = invoke(["status", "--json", "--state-file", str(future)])

    assert result.exit_code == StateError.exit_code
    assert envelope(result)["errors"][0]["code"] == "STATE_ERROR"


# ===========================================================================
# Envelope identity and help
# ===========================================================================


def test_envelope_identifies_itself(invoke, state_file) -> None:
    """The envelope header is how a caller routes a response it did not request.

    Agents run commands in parallel and match results by ``command``; a wrong
    or missing name there is unrecoverable at the far end.
    """
    state_file(**_state_through(2))

    body = envelope(invoke(["status", "--json"]))

    assert body["command"] == "status"
    assert body["status"] == "SUCCESS"
    assert body["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert body["errors"] == []


def test_help_advertises_both_flags(invoke) -> None:
    """``--help`` is how an agent discovers the flags without reading source.

    ``--state-file`` in particular is the only way to run two builds side by
    side, and an undiscoverable option may as well not exist.
    """
    result = invoke(["status", "--help"])

    assert result.exit_code == 0
    assert "--json" in result.output
    assert "--state-file" in result.output
