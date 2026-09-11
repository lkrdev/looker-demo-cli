"""The gated ``demo-create`` workflow, expressed as data rather than as prose.

An orchestrating agent used to learn this pipeline by reading ~400 lines of
``AGENTS.md`` and then *inferring* where in the build it had got to. Prose
drifts from code, and inference from memory is not a contract.

This module is that contract. Each :class:`Gate` carries three things an agent
otherwise has to guess:

1. **Whether the gate is already done**, decided from the persisted
   :class:`~looker_demo_cli.state.FlowState` rather than from recollection.
2. **The literal next command**, with every value the state already knows
   substituted in, and an obvious ``<placeholder>`` wherever it does not.
3. **Whether a human must be asked first**, and exactly what they must confirm.

> The command strings here are executed verbatim by an agent. A wrong flag is
> worse than no feature at all, so each one is built from the real ``typer``
> signature of the command it names, not from the documentation of it.

This module is deliberately **pure**: it reads a ``FlowState`` and returns
plain values. No filesystem access, no network, no ``typer``, no ``rich`` -- so
the caller owns every side effect and every branch is reachable from a unit
test. :mod:`looker_demo_cli.commands.status` is the thin I/O shell around it.

Typical use::

    current = current_gate(state)
    if current is not None:
        print(current.command(state))
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from looker_demo_cli.state import FlowState

# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------
#
# Angle brackets rather than ``{}`` format fields, for two reasons: an agent
# reading `--dataset <dataset-id>` can see it must supply a value, and a string
# containing a stray ``{`` looks like a template that failed to render. A test
# pins that no generated command contains a brace.

_PLACEHOLDER_GCP_PROJECT: Final[str] = "<gcp-project>"
_PLACEHOLDER_DOMAIN: Final[str] = "<domain>"
_PLACEHOLDER_ROW_COUNT: Final[str] = "<row-count>"
_PLACEHOLDER_OUTPUT_DIR: Final[str] = "<output-dir>"
_PLACEHOLDER_PARQUET_DIR: Final[str] = "<parquet-dir>"
_PLACEHOLDER_DATASET: Final[str] = "<dataset-id>"
_PLACEHOLDER_LOOKER_PROJECT: Final[str] = "<looker-project>"
_PLACEHOLDER_CONNECTION: Final[str] = "<connection-name>"
_PLACEHOLDER_LOOKML_DIR: Final[str] = "<lookml-dir>"
_PLACEHOLDER_MODEL: Final[str] = "<model-name>"
_PLACEHOLDER_EXPLORE: Final[str] = "<explore-name>"
_PLACEHOLDER_AGENT_ID: Final[str] = "<agent-id>"

#: Every command string this module emits starts with this, so a caller can
#: assert on it without re-deriving the executable name.
COMMAND_PREFIX: Final[str] = "demo-create "

#: Subdirectory of the generated LookML tree that holds ``*.dashboard.lookml``.
#: ``agent create`` accepts either this directory or its parent; passing the
#: precise one keeps the resolution in the command from depending on layout.
_DASHBOARDS_SUBDIR: Final[str] = "dashboards"


def _resolved(value: str | Path | None, placeholder: str) -> str:
    """Render a state value for interpolation, or fall back to a placeholder.

    Empty strings are treated as unknown, not as values. ``FlowState`` defaults
    several fields from environment-derived config -- ``gcp_project_id`` is
    ``os.getenv("GOOGLE_CLOUD_PROJECT", "")`` -- so an unset environment
    produces ``""``, and interpolating that yields a command with a silently
    missing argument.

    Args:
        value: The state value, which may be absent or blank.
        placeholder: The ``<angle-bracketed>`` token to emit instead.

    Returns:
        The stringified value, or ``placeholder`` when it is absent or blank.
    """
    if value is None:
        return placeholder
    text = str(value).strip()
    return text or placeholder


# ---------------------------------------------------------------------------
# Completion predicates
# ---------------------------------------------------------------------------
#
# Each predicate reads the *one* field the corresponding command actually
# writes on success. Deliberately not a compound condition: a gate that
# required two signals would report incomplete forever if a later refactor
# stopped writing one of them, and an agent would loop re-running a step that
# had already succeeded.


def _gate_0_complete(state: FlowState) -> bool:
    """Whether ``pre-check`` has reported an unblocked environment."""
    return state.precheck_passed


def _gate_1_complete(state: FlowState) -> bool:
    """Whether synthesized tables have reached BigQuery.

    Keyed on the dataset rather than on ``generated_parquet_dir``: local
    Parquet is an intermediate, and modelling reads the warehouse.
    """
    return state.dataset_exists


def _gate_2_complete(state: FlowState) -> bool:
    """Whether ``lookml model`` has written a LookML tree."""
    return state.lookml_output_dir is not None


def _gate_3_complete(state: FlowState) -> bool:
    """Whether a dashboard is live in production.

    The deployed URL, not the project name: ``lookml deploy`` only records the
    URL after the validator and every dashboard tile query have passed.
    """
    return state.deployed_dashboard_url is not None


def _gate_4_complete(state: FlowState) -> bool:
    """Whether a Conversational Analytics agent has been provisioned."""
    return state.ca_agent_id is not None


def _gate_5_complete(state: FlowState) -> bool:
    """Whether the agent has been published to Gemini Enterprise."""
    return state.published_to_ge


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def _gate_0_command(state: FlowState) -> str:
    """Build the gate 0 command: audit the environment, repairing what it can.

    ``--fix`` is always included. Gate 0 is re-entrant -- it is the command an
    agent runs *again* after the user authenticates -- and the repairing form
    is the one the recovery path needs. No ``--json``: like every other gate
    command, the output mode is the caller's choice, not this module's.

    Args:
        state: Current flow state.

    Returns:
        A literal ``demo-create pre-check`` invocation.
    """
    project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
    return f"demo-create pre-check --fix --gcp-project {project}"


def _gate_1_command(state: FlowState) -> str:
    """Build the gate 1 command: synthesize the data, then load it.

    Gate 1 is the one gate with two commands, so the branch is on evidence: if
    Parquet has already been written, the remaining work is the upload, and
    re-running ``data generate`` would discard a dataset the user has already
    reviewed at Phase 2.

    ``--row-count`` stays a placeholder even when tables exist, because the
    volume is a Phase 3 decision belonging to the human, not a value the state
    is entitled to supply on their behalf.

    Args:
        state: Current flow state.

    Returns:
        A literal ``demo-create data upload`` invocation when Parquet exists,
        otherwise a ``demo-create data generate`` invocation.
    """
    if state.generated_parquet_dir is not None:
        parquet_dir = _resolved(state.generated_parquet_dir, _PLACEHOLDER_PARQUET_DIR)
        project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
        dataset = _resolved(state.bq_dataset_id, _PLACEHOLDER_DATASET)
        return f"demo-create data upload --parquet-dir {parquet_dir} --gcp-project {project} --dataset {dataset}"

    domain = _resolved(state.domain_name, _PLACEHOLDER_DOMAIN)
    return (
        f"demo-create data generate --domain {domain} "
        f"--row-count {_PLACEHOLDER_ROW_COUNT} --output-dir {_PLACEHOLDER_OUTPUT_DIR}"
    )


def _gate_2_command(state: FlowState) -> str:
    """Build the gate 2 command: generate views, explores, and dashboards.

    ``--dataset`` is passed explicitly even though ``lookml model`` would fall
    back to state, because the fallback path emits a warning: the dataset name
    is baked into every ``sql_table_name``, so being wrong here produces a
    model that validates cleanly and reads the wrong tables.

    Args:
        state: Current flow state.

    Returns:
        A literal ``demo-create lookml model`` invocation.
    """
    looker_project = _resolved(state.looker_project_name, _PLACEHOLDER_LOOKER_PROJECT)
    dataset = _resolved(state.bq_dataset_id, _PLACEHOLDER_DATASET)
    connection = _resolved(state.looker_connection_name, _PLACEHOLDER_CONNECTION)
    project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
    return (
        f"demo-create lookml model --looker-project {looker_project} "
        f"--dataset {dataset} --connection {connection} --gcp-project {project}"
    )


def _gate_3_command(state: FlowState) -> str:
    """Build the gate 3 command: push, validate, query-test, and release.

    ``--looker-account`` is appended only when an account was recorded. The
    flag is optional and resolves from the saved ``lkr`` session otherwise;
    emitting ``--looker-account <account>`` unconditionally would invite an
    agent to invent an alias that does not exist in ``~/.lkr/auth.db``.

    Args:
        state: Current flow state.

    Returns:
        A literal ``demo-create lookml deploy`` invocation.
    """
    looker_project = _resolved(state.looker_project_name, _PLACEHOLDER_LOOKER_PROJECT)
    lookml_dir = _resolved(state.lookml_output_dir, _PLACEHOLDER_LOOKML_DIR)
    command = f"demo-create lookml deploy --looker-project {looker_project} --lookml-dir {lookml_dir}"
    if state.looker_account:
        command += f" --looker-account {state.looker_account}"
    return command


def _gate_4_command(state: FlowState) -> str:
    """Build the gate 4 command: provision the CA agent and ground it.

    The explore is guessed the same way ``agent create`` guesses it -- first
    generated table, else the model name -- so that the command an agent is
    shown produces the same agent as running the command with the flag omitted.

    ``--dashboards-dir`` is used rather than ``--dashboard-file``: the golden
    queries come from *every* dashboard tile, and the individual filenames are
    not recorded in state.

    Args:
        state: Current flow state.

    Returns:
        A literal ``demo-create agent create`` invocation.
    """
    model = _resolved(state.lookml_model_name, _PLACEHOLDER_MODEL)
    tables = state.generated_tables or state.existing_tables
    explore = _resolved(tables[0] if tables else None, _PLACEHOLDER_EXPLORE)
    command = f"demo-create agent create --model {model} --explore {explore}"
    if state.lookml_output_dir is not None:
        command += f" --dashboards-dir {Path(state.lookml_output_dir) / _DASHBOARDS_SUBDIR}"
    return command


def _gate_5_command(state: FlowState) -> str:
    """Build the gate 5 command: publish the agent to Gemini Enterprise.

    Args:
        state: Current flow state.

    Returns:
        A literal ``demo-create agent publish`` invocation.
    """
    agent_id = _resolved(state.ca_agent_id, _PLACEHOLDER_AGENT_ID)
    return f"demo-create agent publish --agent-id {agent_id}"


# ---------------------------------------------------------------------------
# The gate model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """One stage of the ``demo-create`` pipeline.

    Attributes:
        number: Position in the pipeline, contiguous from 0.
        id: Stable machine-readable identifier, e.g. ``"gate_2_model"``. Safe
            to branch on; the ``title`` is not.
        title: Short human-readable name.
        requires_human_confirmation: Whether an orchestrator must pause and ask
            the user before running :meth:`command`. This is the single field
            the workflow's ``ask_question`` decision hangs on.
        human_checkpoint: What the human must confirm, in one sentence.
            Non-``None`` exactly when ``requires_human_confirmation`` is set --
            "ask the user" without saying what to ask is not actionable.
        _is_complete: Predicate over the persisted state.
        _command: Builds the literal next command from the persisted state.
    """

    number: int
    id: str
    title: str
    requires_human_confirmation: bool
    human_checkpoint: str | None
    _is_complete: Callable[[FlowState], bool]
    _command: Callable[[FlowState], str]

    def is_complete(self, state: FlowState) -> bool:
        """Whether this gate's work is already done.

        Args:
            state: The persisted flow state to judge against.

        Returns:
            True when the state carries this gate's completion signal.
        """
        return self._is_complete(state)

    def command(self, state: FlowState) -> str:
        """The literal command that advances (or re-runs) this gate.

        Args:
            state: The persisted flow state supplying the argument values.

        Returns:
            A runnable ``demo-create`` command, with ``<placeholder>`` tokens
            wherever the state does not yet know a value.
        """
        return self._command(state)

    def describe(self) -> dict[str, Any]:
        """Serialize the state-independent half of this gate.

        Returns:
            A JSON-safe mapping of the gate's identity and human-pause policy.
        """
        return {
            "number": self.number,
            "id": self.id,
            "title": self.title,
            "requires_human_confirmation": self.requires_human_confirmation,
            "human_checkpoint": self.human_checkpoint,
        }


@dataclass(frozen=True)
class GateStatus:
    """A gate judged against a particular state.

    Attributes:
        gate: The gate being reported on.
        complete: Whether its completion signal is present.
        is_current: Whether it is the first incomplete gate, i.e. the one to
            work on now. At most one status in a report has this set.
        command: The literal command for this gate, resolved against the same
            state. Populated for completed gates too, so a caller can re-run
            one without reconstructing the arguments.
    """

    gate: Gate
    complete: bool
    is_current: bool
    command: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the ``--json`` envelope.

        Returns:
            The gate's description plus its verdict against the state.
        """
        return {
            **self.gate.describe(),
            "complete": self.complete,
            "is_current": self.is_current,
            "command": self.command,
        }


GATES: Final[tuple[Gate, ...]] = (
    Gate(
        number=0,
        id="gate_0_environment",
        title="Environment audit & 4-target confirmation",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Confirm all four targets before anything else runs: the GCP user account, the target Google Cloud "
            "project, the Looker instance / OAuth account, and the Looker database connection name."
        ),
        _is_complete=_gate_0_complete,
        _command=_gate_0_command,
    ),
    Gate(
        number=1,
        id="gate_1_data",
        title="Schema co-design, synthesis & BigQuery load",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Confirm the proposed schema and ERD, the 5-10 row micro-sample preview, and the target row volume "
            "before any full-volume synthesis or BigQuery load."
        ),
        _is_complete=_gate_1_complete,
        _command=_gate_1_command,
    ),
    Gate(
        number=2,
        id="gate_2_model",
        title="Semantic modeling & dashboard generation",
        # No pause: the workflow's only branch here -- whether a 3NF snowflake
        # schema needs NDT rollups -- is decided from the schema itself, by a
        # subagent, not by asking the user.
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_2_complete,
        _command=_gate_2_command,
    ),
    Gate(
        number=3,
        id="gate_3_deploy",
        title="Optimization, validation & production release",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Confirm whether to run the LookML performance optimizer (datagroup caching, partition pruning, "
            "foreign key hiding) before validation and the production release."
        ),
        _is_complete=_gate_3_complete,
        _command=_gate_3_command,
    ),
    Gate(
        number=4,
        id="gate_4_agent",
        title="Conversational Analytics agent grounding",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Confirm that a Conversational Analytics agent should be provisioned for the deployed model and "
            "grounded with golden queries extracted from the dashboard tiles."
        ),
        _is_complete=_gate_4_complete,
        _command=_gate_4_command,
    ),
    Gate(
        number=5,
        id="gate_5_publish",
        title="Gemini Enterprise publishing",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Confirm that the Conversational Analytics agent should be published to Gemini Enterprise, which may "
            "also patch Looker settings and grant the Looker service account an IAM role."
        ),
        _is_complete=_gate_5_complete,
        _command=_gate_5_command,
    ),
)


def current_gate(state: FlowState) -> Gate | None:
    """Find the gate to work on now.

    Strictly the **first incomplete** gate in pipeline order, even when a later
    one already looks satisfied. The gates carry real data dependencies -- a
    model cannot be generated from a dataset that was never loaded -- so a
    later signal surviving from an earlier run does not license skipping ahead.

    Args:
        state: The persisted flow state.

    Returns:
        The first gate whose completion signal is absent, or ``None`` when
        every gate is done.
    """
    for gate in GATES:
        if not gate.is_complete(state):
            return gate
    return None


def completed_gates(state: FlowState) -> list[Gate]:
    """List every gate whose completion signal is present.

    Reports observed facts, not a prefix: a gate satisfied out of order still
    appears here. Combined with :func:`current_gate`, which does *not* skip
    ahead, this makes an inconsistent state legible rather than hiding it.

    Args:
        state: The persisted flow state.

    Returns:
        The complete gates, in pipeline order.
    """
    return [gate for gate in GATES if gate.is_complete(state)]


def evaluate_gates(state: FlowState) -> list[GateStatus]:
    """Judge every gate against a state in one pass.

    Args:
        state: The persisted flow state.

    Returns:
        One :class:`GateStatus` per gate, in pipeline order.
    """
    current = current_gate(state)
    return [
        GateStatus(
            gate=gate,
            complete=gate.is_complete(state),
            is_current=current is not None and gate.id == current.id,
            command=gate.command(state),
        )
        for gate in GATES
    ]


def is_pipeline_complete(state: FlowState) -> bool:
    """Whether every gate is done.

    Args:
        state: The persisted flow state.

    Returns:
        True when no gate remains incomplete.
    """
    return current_gate(state) is None
