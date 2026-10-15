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

import shlex
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

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
_PLACEHOLDER_SCHEMA_FILE: Final[str] = "<schema-file>"
_PLACEHOLDER_ROW_COUNT: Final[str] = "<row-count>"
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

#: Declarative mapping of deployment modes to active gate IDs.
MODE_ACTIVE_GATES: Final[dict[str, frozenset[str]]] = {
    "full": frozenset(
        {
            "gate_0a_precheck",
            "gate_0b_confirm_targets",
            "gate_1a_propose_schema",
            "gate_1b_approve_schema",
            "gate_1c_generate_data",
            "gate_1d_catalog",
            "gate_2a_lookml_model",
            "gate_2b_certify_polish",
            "gate_3a_optimize",
            "gate_3b_deploy",
            "gate_3c_critique",
            "gate_4_agent",
            "gate_5_publish",
            "gate_6_embed",
            "gate_7_stream",
        }
    ),
    "bq_only": frozenset(
        {
            "gate_0a_precheck",
            "gate_0b_confirm_targets",
            "gate_1a_propose_schema",
            "gate_1b_approve_schema",
            "gate_1c_generate_data",
            "gate_1d_catalog",
            "gate_7_stream",
        }
    ),
}


# ---------------------------------------------------------------------------
# Declarative Command Rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamBinding:
    """Declarative binding from a CLI flag to a :class:`FlowState` attribute or literal."""

    flag: str
    state_attr: str | tuple[str, ...] | Callable[[FlowState], Any] | None = None
    placeholder: str | None = None
    is_flag: bool = False
    optional: bool = False
    literal: str | None = None

    def _resolve_raw(self, state: FlowState) -> Any:
        if self.state_attr is None:
            return None
        if callable(self.state_attr):
            return self.state_attr(state)
        if isinstance(self.state_attr, tuple):
            for attr in self.state_attr:
                val = getattr(state, attr, None)
                if val is not None and str(val).strip() != "":
                    return val
            return None
        return getattr(state, self.state_attr, None)

    def render(self, state: FlowState) -> str | None:
        """Render this parameter against ``state``, or return ``None`` when omitted."""
        if self.is_flag:
            return self.flag
        if self.literal is not None:
            return f"{self.flag} {self.literal}"
        raw = self._resolve_raw(state)
        if raw is None or str(raw).strip() == "":
            if self.optional:
                return None
            return f"{self.flag} {self.placeholder or '<value>'}"
        text = str(raw).strip()
        val = shlex.quote(text) if any(ch.isspace() for ch in text) else text
        return f"{self.flag} {val}"


@dataclass(frozen=True)
class CommandSpec:
    """Declarative specification for a gate's CLI subcommand and its parameters."""

    subcommand: str
    params: tuple[ParamBinding, ...] = ()
    when: Callable[[FlowState], bool] | None = None
    register_only: bool = False

    def matches(self, state: FlowState) -> bool:
        """Whether this command spec applies to ``state`` for rendering."""
        if self.register_only:
            return False
        return True if self.when is None else self.when(state)

    def render(self, state: FlowState) -> str:
        """Render the full ``demo-create <subcommand> ...`` string against ``state``."""
        parts = [f"{COMMAND_PREFIX}{self.subcommand}"]
        for param in self.params:
            rendered = param.render(state)
            if rendered is not None:
                parts.append(rendered)
        return " ".join(parts)


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


def _gate_1d_complete(state: FlowState) -> bool:
    """Whether Knowledge Catalog metadata was seeded, inspected, synced, or skipped."""
    return (
        state.catalog_status in ("seeded", "inspected", "synced", "skipped") or state.catalog_snapshot_path is not None
    )


def _is_bq_only_complete(state: FlowState) -> bool:
    """Whether the build is in bq_only mode and Gate 5 (catalog) has finished."""
    return state.deployment_mode == "bq_only" and _gate_1d_complete(state)


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


def _gate_7_complete(state: FlowState) -> bool:
    """Whether the optional live BigQuery streaming & CDC service has been deployed or skipped."""
    return state.stream_status in ("deployed", "skipped") or state.stream_deployed


def _resolve_primary_explore(state: FlowState) -> str | None:
    """Resolve the primary explore name from state, preferring primary_explore_name over fct_ tables."""
    if state.primary_explore_name:
        return state.primary_explore_name
    tables = state.generated_tables or state.existing_tables
    if not tables:
        return None
    fact_tables = [t for t in tables if t.startswith("fct_")]
    return fact_tables[0] if fact_tables else tables[0]


# ---------------------------------------------------------------------------
# The gate model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """One stage of the ``demo-create`` pipeline.

    Attributes:
        number: Position in the pipeline, contiguous from 0.
        id: Stable machine-readable identifier, e.g. ``"gate_2a_lookml_model"``.
            Safe to branch on; the ``title`` is not.
        title: Short human-readable name.
        requires_human_confirmation: Whether an orchestrator must pause and ask
            the user before running :meth:`command`. This is the single field
            the workflow's ``ask_question`` decision hangs on.
        human_checkpoint: What the human must confirm, in one sentence.
            Non-``None`` exactly when ``requires_human_confirmation`` is set --
            "ask the user" without saying what to ask is not actionable.
        _is_complete: Predicate over the persisted state.
        _command: Optional legacy command builder fallback.
        command_specs: Declarative command specifications for this gate.
        sample_completion_state: Minimal ``FlowState`` overrides satisfying this gate.
        sample_skip_state: Minimal ``FlowState`` overrides skipping this gate (if optional).
    """

    number: int
    id: str
    title: str
    requires_human_confirmation: bool
    human_checkpoint: str | None
    _is_complete: Callable[[FlowState], bool]
    _command: Callable[[FlowState], str] | None = None
    command_specs: tuple[CommandSpec, ...] = ()
    sample_completion_state: dict[str, Any] = field(default_factory=dict)
    sample_skip_state: dict[str, Any] | None = None

    def is_applicable(self, state: FlowState) -> bool:
        """Whether this gate is active in ``state.deployment_mode``."""
        active = MODE_ACTIVE_GATES.get(state.deployment_mode, MODE_ACTIVE_GATES["full"])
        return self.id in active

    def is_complete(self, state: FlowState) -> bool:
        """Whether this gate's work is already done.

        Args:
            state: The persisted flow state to judge against.

        Returns:
            True when the state carries this gate's completion signal.
        """
        if not self.is_applicable(state) and _is_bq_only_complete(state):
            return True
        rec = state.gates.get(self.id)
        if rec is not None and rec.explicit:
            state.sync_gates()
            rec = state.gates[self.id]
            return rec.is_satisfied
        return self._is_complete(state)

    def command(self, state: FlowState) -> str:
        """The literal command that advances (or re-runs) this gate.

        Args:
            state: The persisted flow state supplying the argument values.

        Returns:
            A runnable ``demo-create`` command, with ``<placeholder>`` tokens
            wherever the state does not yet know a value.
        """
        for spec in self.command_specs:
            if spec.matches(state):
                return spec.render(state)
        if self._command is not None:
            return self._command(state)
        raise RuntimeError(f"No matching CommandSpec or _command for gate {self.id!r}")

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
        applicable: Whether the gate applies to the active deployment mode.
    """

    gate: Gate
    complete: bool
    is_current: bool
    command: str
    applicable: bool = True

    @property
    def status(self) -> Literal["complete", "current", "pending", "not_applicable"]:
        """Canonical 4-state status label for this gate against the evaluated state."""
        if self.is_current:
            return "current"
        if self.complete:
            return "complete"
        if not self.applicable:
            return "not_applicable"
        return "pending"

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


def _number_gates(*gates: Gate) -> tuple[Gate, ...]:
    """Assign contiguous 0-based gate numbers from tuple position."""
    return tuple(replace(gate, number=idx) for idx, gate in enumerate(gates))


GATES: Final[tuple[Gate, ...]] = _number_gates(
    Gate(
        number=0,
        id="gate_0a_precheck",
        title="Environment, credential & skill-tree audit",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_0a_complete,
        command_specs=(
            CommandSpec(
                subcommand="pre-check",
                params=(
                    ParamBinding("--fix", is_flag=True),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                ),
            ),
        ),
        sample_completion_state={"precheck_passed": True},
    ),
    Gate(
        number=1,
        id="gate_0b_confirm_targets",
        title="4-target environment confirmation",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Prompt the user via `ask_question` to select the deployment mode (`full` Looker deployment vs `bq_only` "
            "BigQuery + Knowledge Catalog only) and confirm environment targets: (1) GCP user account, (2) target "
            "Google Cloud project ID, and when `mode=full`: (3) Looker instance / OAuth account, and (4) Looker "
            "database connection name. Then record them with `demo-create confirm-targets`."
        ),
        _is_complete=_gate_0b_complete,
        command_specs=(
            CommandSpec(
                subcommand="confirm-targets",
                when=lambda s: s.deployment_mode == "bq_only",
                params=(
                    ParamBinding("--mode", literal="bq_only"),
                    ParamBinding("--gcp-account", "gcp_account", _PLACEHOLDER_GCP_ACCOUNT),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                ),
            ),
            CommandSpec(
                subcommand="confirm-targets",
                params=(
                    ParamBinding("--gcp-account", "gcp_account", _PLACEHOLDER_GCP_ACCOUNT),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                    ParamBinding("--looker-account", "looker_account", _PLACEHOLDER_LOOKER_ACCOUNT),
                    ParamBinding("--connection", "looker_connection_name", _PLACEHOLDER_CONNECTION),
                ),
            ),
        ),
        sample_completion_state={"targets_confirmed": True},
    ),
    Gate(
        number=2,
        id="gate_1a_propose_schema",
        title="Schema blueprint authoring & 5-row micro-sample preview",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_1a_complete,
        command_specs=(
            CommandSpec(
                subcommand="data propose-schema",
                params=(
                    ParamBinding("--schema-file", "schema_file_path", _PLACEHOLDER_SCHEMA_FILE),
                    ParamBinding("--preview", is_flag=True),
                ),
            ),
        ),
        sample_completion_state={"schema_proposed": True},
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
        command_specs=(
            CommandSpec(
                subcommand="data approve-schema",
                params=(
                    ParamBinding("--row-count", "approved_row_count", _PLACEHOLDER_ROW_COUNT),
                    ParamBinding("--dataset", ("bq_dataset_id", "domain_name"), _PLACEHOLDER_DATASET),
                ),
            ),
        ),
        sample_completion_state={"schema_approved": True},
    ),
    Gate(
        number=4,
        id="gate_1c_generate_data",
        title="Modular DAG synthesis & BigQuery load",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_1c_complete,
        command_specs=(
            CommandSpec(
                subcommand="data upload",
                when=lambda s: s.generated_parquet_dir is not None,
                params=(
                    ParamBinding("--parquet-dir", "generated_parquet_dir", _PLACEHOLDER_PARQUET_DIR),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                    ParamBinding("--dataset", "bq_dataset_id", _PLACEHOLDER_DATASET),
                ),
            ),
            CommandSpec(
                subcommand="data generate",
                params=(
                    ParamBinding("--schema-file", "schema_file_path", _PLACEHOLDER_SCHEMA_FILE),
                    ParamBinding("--row-count", "approved_row_count", _PLACEHOLDER_ROW_COUNT),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                    ParamBinding("--dataset", ("bq_dataset_id", "domain_name"), _PLACEHOLDER_DATASET),
                    ParamBinding("--upload", is_flag=True),
                    ParamBinding("--json-scorecard", is_flag=True),
                ),
            ),
            CommandSpec(subcommand="data adopt", register_only=True),
        ),
        sample_completion_state={"dataset_exists": True},
    ),
    Gate(
        number=5,
        id="gate_1d_catalog",
        title="Knowledge Catalog semantic curation & enrichment",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm Knowledge Catalog (Dataplex) enrichment before LookML modeling: "
            "for net-new datasets, prompt to seed semantic curation aspect types and business glossaries "
            "(`demo-create catalog seed --dataset <dataset-id> --gcp-project <gcp-project>`); for existing datasets, "
            "prompt to inspect Dataplex metadata (`demo-create catalog inspect --dataset <dataset-id> --gcp-project <gcp-project>`); "
            "for existing LookML projects, sync from Knowledge Catalog (`demo-create catalog sync ...`); "
            "or skip (`demo-create catalog seed --skip` / `demo-create catalog inspect --skip`)."
        ),
        _is_complete=_gate_1d_complete,
        command_specs=(
            CommandSpec(
                subcommand="catalog seed",
                when=lambda s: s.data_source_mode == "synthetic",
                params=(
                    ParamBinding("--dataset", ("bq_dataset_id", "domain_name"), _PLACEHOLDER_DATASET),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                ),
            ),
            CommandSpec(
                subcommand="catalog inspect",
                params=(
                    ParamBinding("--dataset", ("bq_dataset_id", "domain_name"), _PLACEHOLDER_DATASET),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                ),
            ),
            CommandSpec(subcommand="catalog sync", register_only=True),
        ),
        sample_completion_state={"catalog_status": "seeded"},
        sample_skip_state={"catalog_status": "skipped"},
    ),
    Gate(
        number=6,
        id="gate_2a_lookml_model",
        title="Semantic LookML modeling & draft dashboard scaffolding",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_2a_complete,
        command_specs=(
            CommandSpec(
                subcommand="lookml model",
                params=(
                    ParamBinding("--looker-project", "looker_project_name", _PLACEHOLDER_LOOKER_PROJECT),
                    ParamBinding("--dataset", "bq_dataset_id", _PLACEHOLDER_DATASET),
                    ParamBinding("--connection", "looker_connection_name", _PLACEHOLDER_CONNECTION),
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                    ParamBinding("--catalog", "catalog_snapshot_path", optional=True),
                ),
            ),
        ),
        sample_completion_state={"lookml_output_dir": Path("/scratch/lookml")},
    ),
    Gate(
        number=7,
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
        command_specs=(
            CommandSpec(
                subcommand="lookml certify-polish",
                params=(ParamBinding("--lookml-dir", "lookml_output_dir", _PLACEHOLDER_LOOKML_DIR),),
            ),
        ),
        sample_completion_state={"polish_certified": True},
    ),
    Gate(
        number=8,
        id="gate_3a_optimize",
        title="LookML Server Performance Optimizer gate",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether to run the Google Cloud Looker Server Performance Optimizer "
            "(`demo-create lookml optimize --lookml-dir <dir>`) or skip optimization "
            "(`demo-create lookml optimize --lookml-dir <dir> --skip`)."
        ),
        _is_complete=_gate_3a_complete,
        command_specs=(
            CommandSpec(
                subcommand="lookml optimize",
                params=(ParamBinding("--lookml-dir", "lookml_output_dir", _PLACEHOLDER_LOOKML_DIR),),
            ),
        ),
        sample_completion_state={"optimizer_status": "applied"},
        sample_skip_state={"optimizer_status": "skipped"},
    ),
    Gate(
        number=9,
        id="gate_3b_deploy",
        title="Pre-deployment LookML validation, query testing & production release",
        requires_human_confirmation=False,
        human_checkpoint=None,
        _is_complete=_gate_3b_complete,
        command_specs=(
            CommandSpec(
                subcommand="lookml deploy",
                params=(
                    ParamBinding("--looker-project", "looker_project_name", _PLACEHOLDER_LOOKER_PROJECT),
                    ParamBinding("--lookml-dir", "lookml_output_dir", _PLACEHOLDER_LOOKML_DIR),
                    ParamBinding("--looker-account", "looker_account", optional=True),
                ),
            ),
        ),
        sample_completion_state={"deployed_dashboard_url": "https://acme.looker.com/dashboards/42"},
    ),
    Gate(
        number=10,
        id="gate_3c_critique",
        title="Post-deploy dashboard screenshot critique (Pass 3)",
        requires_human_confirmation=True,
        human_checkpoint=(
            "MANDATORY POST-DEPLOY CRITIQUE: Present the live `deployed_dashboard_url` in chat and call `ask_question` "
            "inviting the user to upload a screenshot for Pass 3 visual critique/refinement or approve the dashboard "
            "layout as-is. Once approved, run `demo-create lookml approve-critique`."
        ),
        _is_complete=_gate_3c_complete,
        command_specs=(
            CommandSpec(
                subcommand="lookml approve-critique",
                params=(ParamBinding("--looker-project", "looker_project_name", _PLACEHOLDER_LOOKER_PROJECT),),
            ),
        ),
        sample_completion_state={"critique_approved": True},
    ),
    Gate(
        number=11,
        id="gate_4_agent",
        title="Conversational Analytics agent provisioning & golden queries",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether to provision a Looker Conversational Analytics agent grounded "
            "with golden queries extracted from the dashboard tiles (`demo-create agent create ...`) or skip "
            "(`demo-create agent create --skip`)."
        ),
        _is_complete=_gate_4_complete,
        command_specs=(
            CommandSpec(
                subcommand="agent create",
                params=(
                    ParamBinding("--model", "lookml_model_name", _PLACEHOLDER_MODEL),
                    ParamBinding("--explore", _resolve_primary_explore, _PLACEHOLDER_EXPLORE),
                    ParamBinding(
                        "--dashboards-dir",
                        lambda s: (
                            Path(s.lookml_output_dir) / _DASHBOARDS_SUBDIR if s.lookml_output_dir is not None else None
                        ),
                        optional=True,
                    ),
                ),
            ),
        ),
        sample_completion_state={"ca_agent_id": "agent-42", "ca_agent_status": "created"},
        sample_skip_state={"ca_agent_id": None, "ca_agent_status": "skipped"},
    ),
    Gate(
        number=12,
        id="gate_5_publish",
        title="Gemini Enterprise publishing",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether the Conversational Analytics agent should be published to "
            "Gemini Enterprise (`demo-create agent publish --agent-id <id>`) or skipped "
            "(`demo-create agent publish --skip`)."
        ),
        _is_complete=_gate_5_complete,
        command_specs=(
            CommandSpec(
                subcommand="agent publish",
                params=(ParamBinding("--agent-id", "ca_agent_id", _PLACEHOLDER_AGENT_ID),),
            ),
            CommandSpec(subcommand="ge publish", register_only=True),
        ),
        sample_completion_state={"published_to_ge": True, "ge_publish_status": "published"},
        sample_skip_state={"published_to_ge": False, "ge_publish_status": "skipped"},
    ),
    Gate(
        number=13,
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
        command_specs=(
            CommandSpec(
                subcommand="embed scaffold",
                params=(ParamBinding("--looker-project", "looker_project_name", _PLACEHOLDER_LOOKER_PROJECT),),
            ),
        ),
        sample_completion_state={"embed_workspace_dir": Path("/scratch/embed"), "embed_status": "scaffolded"},
        sample_skip_state={"embed_workspace_dir": None, "embed_status": "skipped"},
    ),
    Gate(
        number=14,
        id="gate_7_stream",
        title="Live BigQuery streaming & CDC GCE deployment (optional)",
        requires_human_confirmation=True,
        human_checkpoint=(
            "Call `ask_question` to confirm whether to deploy a live BigQuery streaming & CDC service on a locked-down "
            "GCE VM (`demo-create stream plan --gcp-project <gcp-project> --dataset <dataset-id>`) or skip "
            "(`demo-create stream plan --skip`). Note: While the streaming daemon runs on a GCE free-tier `e2-micro` "
            "compute instance, continuous streaming into BigQuery will accumulate rows and storage/write volume over "
            "time that may lead to GCP costs being incurred. Consider running `demo-create stream delete` once the "
            "need for your demo resource is done."
        ),
        _is_complete=_gate_7_complete,
        command_specs=(
            CommandSpec(
                subcommand="stream deploy",
                when=lambda s: s.stream_status == "planned" or s.stream_planned,
                params=(
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                    ParamBinding("--dataset", ("bq_dataset_id", "domain_name"), _PLACEHOLDER_DATASET),
                ),
            ),
            CommandSpec(
                subcommand="stream plan",
                params=(
                    ParamBinding("--gcp-project", "gcp_project_id", _PLACEHOLDER_GCP_PROJECT),
                    ParamBinding("--dataset", ("bq_dataset_id", "domain_name"), _PLACEHOLDER_DATASET),
                ),
            ),
        ),
        sample_completion_state={"stream_status": "deployed", "stream_deployed": True},
        sample_skip_state={"stream_status": "skipped", "stream_deployed": False},
    ),
)

_GATES_BY_ID: Final[dict[str, Gate]] = {gate.id: gate for gate in GATES}


def gate_by_id(gate_id: str) -> Gate:
    """Look up a :class:`Gate` by its stable machine-readable ID.

    Args:
        gate_id: The gate identifier (e.g. ``"gate_2a_lookml_model"``).

    Returns:
        The matching :class:`Gate`.

    Raises:
        KeyError: If ``gate_id`` is not a registered gate.
    """
    return _GATES_BY_ID[gate_id]


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
            applicable=gate.is_applicable(state),
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


_COMMAND_TO_GATE_ID: Final[dict[str, str]] = {spec.subcommand: gate.id for gate in GATES for spec in gate.command_specs}


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
