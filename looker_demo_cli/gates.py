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
_PLACEHOLDER_GCP_ACCOUNT: Final[str] = "<gcp-account>"
_PLACEHOLDER_LOOKER_ACCOUNT: Final[str] = "<looker-account>"
_PLACEHOLDER_DOMAIN: Final[str] = "<domain>"
_PLACEHOLDER_SCHEMA_FILE: Final[str] = "<schema-file>"
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


def _resolved(value: str | int | Path | None, placeholder: str) -> str:
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
# Each predicate reads the *one* field (or canonical status union) the
# corresponding command writes on success.


def _gate_0a_complete(state: FlowState) -> bool:
    """Whether ``pre-check`` has reported an unblocked environment."""
    return state.precheck_passed


def _gate_0b_complete(state: FlowState) -> bool:
    """Whether the human has confirmed the 4 environment targets via ``confirm-targets``."""
    return state.targets_confirmed


def _gate_1a_complete(state: FlowState) -> bool:
    """Whether a schema blueprint has been proposed and micro-sampled via ``data propose-schema``."""
    return state.schema_proposed or state.dataset_exists


def _gate_1b_complete(state: FlowState) -> bool:
    """Whether the proposed schema and target row volume have been approved via ``data approve-schema``."""
    return state.schema_approved or state.dataset_exists


def _gate_1c_complete(state: FlowState) -> bool:
    """Whether synthesized tables have reached BigQuery."""
    return state.dataset_exists


def _gate_2a_complete(state: FlowState) -> bool:
    """Whether ``lookml model`` has written a LookML tree."""
    return state.lookml_output_dir is not None


def _gate_2b_complete(state: FlowState) -> bool:
    """Whether the 3-Pass Executive Dashboard Polish and filtered measure checks passed ``lookml certify-polish``."""
    return state.polish_certified


def _gate_3a_complete(state: FlowState) -> bool:
    """Whether the human has made an explicit decision on ``lookml optimize`` (applied or skipped)."""
    return state.optimizer_status in ("applied", "skipped")


def _gate_3b_complete(state: FlowState) -> bool:
    """Whether a dashboard is live in production."""
    return state.deployed_dashboard_url is not None


def _gate_3c_complete(state: FlowState) -> bool:
    """Whether the post-deploy visual layout critique (Pass 3) has been approved via ``lookml approve-critique``."""
    return state.critique_approved


def _gate_4_complete(state: FlowState) -> bool:
    """Whether a Conversational Analytics agent has been provisioned or explicitly skipped."""
    return state.ca_agent_id is not None or state.ca_agent_status in ("created", "skipped")


def _gate_5_complete(state: FlowState) -> bool:
    """Whether the agent has been published to Gemini Enterprise or explicitly skipped."""
    return (
        state.published_to_ge
        or state.ge_publish_status in ("published", "skipped")
        or state.ca_agent_status == "skipped"
    )


def _gate_6_complete(state: FlowState) -> bool:
    """Whether the external embed portal has been scaffolded or explicitly skipped."""
    return state.embed_workspace_dir is not None or state.embed_status in ("scaffolded", "skipped")


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------


def _gate_0a_command(state: FlowState) -> str:
    """Build the gate 0A command: audit the environment, repairing what it can."""
    project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
    return f"demo-create pre-check --fix --gcp-project {project}"


def _gate_0b_command(state: FlowState) -> str:
    """Build the gate 0B command: record the 4 human-confirmed environment targets."""
    account = _resolved(state.gcp_account, _PLACEHOLDER_GCP_ACCOUNT)
    project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
    looker_account = _resolved(state.looker_account, _PLACEHOLDER_LOOKER_ACCOUNT)
    connection = _resolved(state.looker_connection_name, _PLACEHOLDER_CONNECTION)
    return (
        f"demo-create confirm-targets --gcp-account {account} "
        f"--gcp-project {project} --looker-account {looker_account} --connection {connection}"
    )


def _gate_1a_command(state: FlowState) -> str:
    """Build the gate 1A command: validate schema blueprint, generate 5-row micro-sample & Mermaid ERD."""
    schema_file = _resolved(state.schema_file_path, _PLACEHOLDER_SCHEMA_FILE)
    return f"demo-create data propose-schema --schema-file {schema_file} --preview"


def _gate_1b_command(state: FlowState) -> str:
    """Build the gate 1B command: record user approval of the schema and target row volume."""
    row_count = _resolved(state.approved_row_count, _PLACEHOLDER_ROW_COUNT)
    dataset = _resolved(state.bq_dataset_id or state.domain_name, _PLACEHOLDER_DATASET)
    return f"demo-create data approve-schema --row-count {row_count} --dataset {dataset}"


def _gate_1c_command(state: FlowState) -> str:
    """Build the gate 1C command: synthesize the approved dataset and load it into BigQuery."""
    if state.generated_parquet_dir is not None:
        parquet_dir = _resolved(state.generated_parquet_dir, _PLACEHOLDER_PARQUET_DIR)
        project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
        dataset = _resolved(state.bq_dataset_id, _PLACEHOLDER_DATASET)
        return f"demo-create data upload --parquet-dir {parquet_dir} --gcp-project {project} --dataset {dataset}"

    schema_file = _resolved(state.schema_file_path, _PLACEHOLDER_SCHEMA_FILE)
    row_count = _resolved(state.approved_row_count, _PLACEHOLDER_ROW_COUNT)
    project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
    dataset = _resolved(state.bq_dataset_id or state.domain_name, _PLACEHOLDER_DATASET)
    return (
        f"demo-create data generate --schema-file {schema_file} "
        f"--row-count {row_count} --gcp-project {project} --dataset {dataset} "
        f"--upload --json-scorecard"
    )


def _gate_2a_command(state: FlowState) -> str:
    """Build the gate 2A command: generate views, explores, and draft dashboards."""
    looker_project = _resolved(state.looker_project_name, _PLACEHOLDER_LOOKER_PROJECT)
    dataset = _resolved(state.bq_dataset_id, _PLACEHOLDER_DATASET)
    connection = _resolved(state.looker_connection_name, _PLACEHOLDER_CONNECTION)
    project = _resolved(state.gcp_project_id, _PLACEHOLDER_GCP_PROJECT)
    return (
        f"demo-create lookml model --looker-project {looker_project} "
        f"--dataset {dataset} --connection {connection} --gcp-project {project}"
    )


def _gate_2b_command(state: FlowState) -> str:
    """Build the gate 2B command: certify 3-Pass Dashboard Polish and filtered measure grounding."""
    lookml_dir = _resolved(state.lookml_output_dir, _PLACEHOLDER_LOOKML_DIR)
    return f"demo-create lookml certify-polish --lookml-dir {lookml_dir}"


def _gate_3a_command(state: FlowState) -> str:
    """Build the gate 3A command: run (or skip with --skip) the LookML server performance optimizer."""
    lookml_dir = _resolved(state.lookml_output_dir, _PLACEHOLDER_LOOKML_DIR)
    return f"demo-create lookml optimize --lookml-dir {lookml_dir}"


def _gate_3b_command(state: FlowState) -> str:
    """Build the gate 3B command: push, validate, query-test, and release to production."""
    looker_project = _resolved(state.looker_project_name, _PLACEHOLDER_LOOKER_PROJECT)
    lookml_dir = _resolved(state.lookml_output_dir, _PLACEHOLDER_LOOKML_DIR)
    command = f"demo-create lookml deploy --looker-project {looker_project} --lookml-dir {lookml_dir}"
    if state.looker_account:
        command += f" --looker-account {state.looker_account}"
    return command


def _gate_3c_command(state: FlowState) -> str:
    """Build the gate 3C command: record user approval of the rendered dashboard layout (Pass 3 critique)."""
    looker_project = _resolved(state.looker_project_name, _PLACEHOLDER_LOOKER_PROJECT)
    return f"demo-create lookml approve-critique --looker-project {looker_project}"


def _resolve_primary_explore(state: FlowState) -> str | None:
    """Resolve the primary explore name from state, preferring primary_explore_name over fct_ tables."""
    if state.primary_explore_name:
        return state.primary_explore_name
    tables = state.generated_tables or state.existing_tables
    if not tables:
        return None
    fact_tables = [t for t in tables if t.startswith("fct_")]
    return fact_tables[0] if fact_tables else tables[0]


def _gate_4_command(state: FlowState) -> str:
    """Build the gate 4 command: provision the CA agent and ground it (or pass --skip)."""
    model = _resolved(state.lookml_model_name, _PLACEHOLDER_MODEL)
    explore = _resolved(_resolve_primary_explore(state), _PLACEHOLDER_EXPLORE)
    command = f"demo-create agent create --model {model} --explore {explore}"
    if state.lookml_output_dir is not None:
        command += f" --dashboards-dir {Path(state.lookml_output_dir) / _DASHBOARDS_SUBDIR}"
    return command


def _gate_5_command(state: FlowState) -> str:
    """Build the gate 5 command: publish the agent to Gemini Enterprise (or pass --skip)."""
    agent_id = _resolved(state.ca_agent_id, _PLACEHOLDER_AGENT_ID)
    return f"demo-create agent publish --agent-id {agent_id}"


def _gate_6_command(state: FlowState) -> str:
    """Build the gate 6 command: scaffold the external embedded analytics portal (or pass --skip)."""
    looker_project = _resolved(state.looker_project_name, _PLACEHOLDER_LOOKER_PROJECT)
    return f"demo-create embed scaffold --looker-project {looker_project}"


# ---------------------------------------------------------------------------
# The gate model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """One stage of the ``demo-create`` pipeline.

    Attributes:
        number: Position in the pipeline, contiguous from 0.
        id: Stable machine-readable identifier, e.g. ``"gate_2a_model"``. Safe
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
        id="gate_0a_precheck",
        title="Environment, credential & skill-tree audit",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_0a_complete,
        _command=_gate_0a_command,
    ),
    Gate(
        number=1,
        id="gate_0b_confirm_targets",
        title="4-target environment confirmation",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Prompt the user via `ask_question` to confirm all four environment targets before designing schemas "
            "or running any synthesis: (1) GCP user account, (2) target Google Cloud project ID, (3) Looker instance "
            "/ OAuth account, and (4) Looker database connection name. Then record them with `demo-create confirm-targets`."
        ),
        _is_complete=_gate_0b_complete,
        _command=_gate_0b_command,
    ),
    Gate(
        number=2,
        id="gate_1a_propose_schema",
        title="Schema blueprint authoring & 5-row micro-sample preview",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_1a_complete,
        _command=_gate_1a_command,
    ),
    Gate(
        number=3,
        id="gate_1b_approve_schema",
        title="Schema ERD & target row volume approval",
        requires_human_confirmation=True,
        human_checkpoint=(
            "MANDATORY: Write out the full proposed relational schema (tables, columns, types, primary/foreign keys), "
            "Mermaid ERD diagram (`data.mermaid_erd`), and 5-row micro-sample preview (`data.samples`) directly in "
            "visible chat text FIRST. Only AFTER rendering the schema and preview in chat, call `ask_question` to "
            "confirm user approval and target row volume, then record approval via `demo-create data approve-schema`."
        ),
        _is_complete=_gate_1b_complete,
        _command=_gate_1b_command,
    ),
    Gate(
        number=4,
        id="gate_1c_generate_data",
        title="Modular DAG synthesis & BigQuery load",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_1c_complete,
        _command=_gate_1c_command,
    ),
    Gate(
        number=5,
        id="gate_2a_lookml_model",
        title="Semantic LookML modeling & draft dashboard scaffolding",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_2a_complete,
        _command=_gate_2a_command,
    ),
    Gate(
        number=6,
        id="gate_2b_certify_polish",
        title="3-Pass Executive Dashboard Polish & filtered measure audit",
        requires_human_confirmation=True,
        human_checkpoint=(
            "MANDATORY DASHBOARD POLISH: Do NOT deploy the raw scaffold. Invoke the `looker-visualizations` "
            "skill suite (or `lookml-dashboard-designer` subagent) to tailor chart types, custom palettes, "
            "KPI single-values, and modern Highcharts tokens to the domain. Run `demo-create lookml certify-polish` "
            "and review the design with the user before proceeding."
        ),
        _is_complete=_gate_2b_complete,
        _command=_gate_2b_command,
    ),
    Gate(
        number=7,
        id="gate_3a_optimize",
        title="LookML Server Performance Optimizer gate",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether to run the Google Cloud Looker Server Performance Optimizer "
            "(`demo-create lookml optimize --lookml-dir <dir>`) or skip optimization "
            "(`demo-create lookml optimize --lookml-dir <dir> --skip`)."
        ),
        _is_complete=_gate_3a_complete,
        _command=_gate_3a_command,
    ),
    Gate(
        number=8,
        id="gate_3b_deploy",
        title="Pre-deployment LookML validation, query testing & production release",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_3b_complete,
        _command=_gate_3b_command,
    ),
    Gate(
        number=9,
        id="gate_3c_critique",
        title="Post-deploy dashboard screenshot critique (Pass 3)",
        requires_human_confirmation=True,
        human_checkpoint=(
            "MANDATORY POST-DEPLOY CRITIQUE: Present the live `deployed_dashboard_url` in chat and call `ask_question` "
            "inviting the user to upload a screenshot for Pass 3 visual critique/refinement or approve the dashboard "
            "layout as-is. Once approved, run `demo-create lookml approve-critique`."
        ),
        _is_complete=_gate_3c_complete,
        _command=_gate_3c_command,
    ),
    Gate(
        number=10,
        id="gate_4_agent",
        title="Conversational Analytics agent provisioning & golden queries",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether to provision a Looker Conversational Analytics agent grounded "
            "with golden queries extracted from the dashboard tiles (`demo-create agent create ...`) or skip "
            "(`demo-create agent create --skip`)."
        ),
        _is_complete=_gate_4_complete,
        _command=_gate_4_command,
    ),
    Gate(
        number=11,
        id="gate_5_publish",
        title="Gemini Enterprise publishing",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether the Conversational Analytics agent should be published to "
            "Gemini Enterprise (`demo-create agent publish --agent-id <id>`) or skipped "
            "(`demo-create agent publish --skip`)."
        ),
        _is_complete=_gate_5_complete,
        _command=_gate_5_command,
    ),
    Gate(
        number=12,
        id="gate_6_embed",
        title="External Embedded Analytics portal scaffolding",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether to scaffold the external branded embedded analytics portal "
            "or skip (`demo-create embed scaffold --skip`). If confirmed, prompt for Looker API Service Account "
            "credentials (`--client-id` and `--client-secret` for `LOOKERSDK_CLIENT_ID` / `LOOKERSDK_CLIENT_SECRET` in `backend/.env`), "
            "run `demo-create embed scaffold --looker-project <project> --client-id <id> --client-secret <secret>`, "
            "and verify all 6 Looker instance provisioning checks (SA auth, embed group, shared folder, "
            "`PUT /api/4.0/lookml_dashboards/move`, CA agent sharing, and `<Brand>_Light`/`<Brand>_Dark` themes)."
        ),
        _is_complete=_gate_6_complete,
        _command=_gate_6_command,
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


_COMMAND_TO_GATE_ID: Final[dict[str, str]] = {
    "pre-check": "gate_0a_precheck",
    "confirm-targets": "gate_0b_confirm_targets",
    "data propose-schema": "gate_1a_propose_schema",
    "data approve-schema": "gate_1b_approve_schema",
    "data generate": "gate_1c_generate_data",
    "data upload": "gate_1c_generate_data",
    "lookml model": "gate_2a_lookml_model",
    "lookml certify-polish": "gate_2b_certify_polish",
    "lookml optimize": "gate_3a_optimize",
    "lookml deploy": "gate_3b_deploy",
    "lookml approve-critique": "gate_3c_critique",
    "agent create": "gate_4_agent",
    "agent publish": "gate_5_publish",
    "ge publish": "gate_5_publish",
    "embed scaffold": "gate_6_embed",
}


def attach_next_gate_action(result: Any, state: FlowState) -> Any:
    """Populate ``result.next_actions`` from the first incomplete gate at or after
    the command's own stage so command envelopes and ``demo-create status --json``
    share one source of truth.

    Args:
        result: A :class:`~looker_demo_cli.output.CommandResult` instance.
        state: The updated :class:`FlowState`.

    Returns:
        The mutated ``result`` for chaining.
    """
    cmd_name = getattr(result, "command", "")
    gate_id = _COMMAND_TO_GATE_ID.get(cmd_name)
    start_idx = 0
    if gate_id is not None:
        for idx, g in enumerate(GATES):
            if g.id == gate_id:
                start_idx = idx
                break

    nxt: Gate | None = None
    for gate in GATES[start_idx:]:
        if not gate.is_complete(state):
            nxt = gate
            break

    if nxt is not None:
        result.add_next_action(
            f"Gate {nxt.number} ({nxt.id}): {nxt.title}",
            nxt.command(state),
            gate=nxt.number,
            requires_human_confirmation=nxt.requires_human_confirmation,
        )
    return result


