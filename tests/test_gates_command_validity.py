"""Proves every command string in :mod:`looker_demo_cli.gates` is really runnable.

**The invariant under test: the strings in ``gates.py`` are executed verbatim by
an AI agent, so they are checked against the parser rather than against another
hand-written string.**

``tests/test_gates.py`` pins those strings character for character -- but it
pins them against literals written in the same sitting as the module. If a flag
name is wrong in both places, every one of those tests passes and the agent
still runs a command that dies with Click's exit code 2, reads a usage error it
did not expect, and starts improvising. The flags *do* move: this refactor
renamed ``--project`` to ``--looker-project`` mid-flight, and ``--connection``
on ``lookml model`` changed default in the same phase -- a wrong-but-accepted
connection produces a model that deploys and then cannot run a single query.

So this module asserts against the only source of truth that cannot drift: the
Click command tree the installed CLI actually builds. For all six gates, in both
an unstarted and a fully populated state, it resolves the subcommand path and
checks every flag against the target command's declared parameters.

Introspection, never invocation
-------------------------------
Nothing here runs a command. ``typer.main.get_command(app)`` returns the Click
object graph, and ``Parameter.opts`` lists the declared spellings. That is
enough to prove a command *would* parse, and it is what keeps this file
hermetic -- the commands under inspection talk to BigQuery and Looker, so a
``CliRunner`` here would be a network call, not a test.

The helpers deliberately avoid importing ``click`` and avoid ``isinstance``
checks: Typer vendors a fork of Click (see
:mod:`looker_demo_cli.error_boundary`), so a test written against the top-level
package can silently stop matching. Duck typing on ``.commands`` and ``.params``
holds for both.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command

from looker_demo_cli.cli import app
from looker_demo_cli.gates import GATES
from looker_demo_cli.state import FlowState

pytestmark = pytest.mark.unit

#: The real Click command tree, built exactly as the installed ``demo-create``
#: binary builds it.
_CLI: Any = get_command(app)

_EXECUTABLE = "demo-create"


# ===========================================================================
# State builders
#
# Duplicated from test_gates.py rather than imported: `tests/` is not a
# package, and a shared helper module would couple two files that are meant to
# fail independently -- this one when the CLI changes, that one when the
# generated strings change.
# ===========================================================================


def _unstarted_state() -> FlowState:
    """A state in which nothing has run yet, so every value is a placeholder."""
    return FlowState(gcp_project_id="")


def _finished_state() -> FlowState:
    """A state in which every value a command interpolates is known."""
    return FlowState(
        precheck_passed=True,
        gcp_project_id="acme-analytics",
        looker_account="acme-looker",
        looker_connection_name="acme_bigquery",
        dataset_exists=True,
        bq_dataset_id="retail",
        domain_name="retail",
        generated_parquet_dir=Path("/scratch/retail"),
        generated_tables=["fct_orders", "dim_users"],
        looker_project_name="retail_demo",
        lookml_model_name="retail_demo",
        lookml_output_dir=Path("/scratch/lookml_retail_demo"),
        existing_tables=["fct_orders", "dim_users"],
        deployed_dashboard_url="https://acme.looker.com/dashboards/42",
        ca_agent_id="agent-42",
        published_to_ge=True,
    )


_STATE_BUILDERS = {
    "unstarted": _unstarted_state,
    "populated": _finished_state,
}

#: Every gate crossed with every state, which is the full matrix of command
#: strings the module can emit. Both halves matter: the placeholder branch and
#: the interpolated branch build their flag lists on different code paths.
_CASES = [
    pytest.param(gate, builder, id=f"{gate.id}-{label}") for gate in GATES for label, builder in _STATE_BUILDERS.items()
]

_GATE_IDS = [gate.id for gate in GATES]


# ===========================================================================
# Click introspection helpers
# ===========================================================================


def _child(node: Any, name: str) -> Any | None:
    """Look up a subcommand by name.

    Reads ``.commands`` directly rather than calling ``get_command(ctx, name)``,
    which would need a Click ``Context`` -- and constructing one means choosing
    between the real ``click`` package and Typer's vendored fork. Typer
    populates ``.commands`` eagerly at ``get_command(app)`` time, so the dict is
    always there and always complete.

    Args:
        node: A Click group, or any command.
        name: The subcommand name to look for.

    Returns:
        The child command, or ``None`` when ``node`` is not a group or has no
        such child.
    """
    commands = getattr(node, "commands", None)
    if not isinstance(commands, dict):
        return None
    return commands.get(name)


def _is_group(node: Any) -> bool:
    """Whether a command can hold subcommands."""
    return isinstance(getattr(node, "commands", None), dict)


def _resolve(argv: list[str]) -> tuple[Any, list[str], list[str]]:
    """Walk ``argv`` down the command tree as Click would.

    Consumes leading non-flag tokens for as long as they name real
    subcommands. Everything after that is the command's own argument list.

    Args:
        argv: Tokens after the executable name.

    Returns:
        ``(command, path, remaining)`` -- the deepest command reached, the
        subcommand names consumed to reach it, and the unconsumed tokens.
    """
    node: Any = _CLI
    path: list[str] = []
    index = 0
    while index < len(argv) and not argv[index].startswith("-"):
        child = _child(node, argv[index])
        if child is None:
            break
        node = child
        path.append(argv[index])
        index += 1
    return node, path, argv[index:]


def _declared_option_names(command: Any) -> set[str]:
    """Collect every option spelling a command declares.

    Includes ``secondary_opts`` so that a paired flag such as
    ``--backup/--no-backup`` is recognised under either name.

    Args:
        command: A Click command.

    Returns:
        The declared dashed option names.
    """
    names: set[str] = set()
    for param in command.params:
        for opt in (*param.opts, *param.secondary_opts):
            if opt.startswith("-"):
                names.add(opt)
    return names


def _split_arguments(command: Any, tokens: list[str]) -> tuple[list[str], list[str]]:
    """Separate flags from positional arguments the way Click's parser would.

    Whether a token is a *value* or a *positional* depends on the preceding
    flag: ``--domain retail`` supplies a value, ``--fix retail`` would leave
    ``retail`` stranded as a positional. Consulting the declared parameter for
    ``is_flag`` is what makes the distinction, and it is why this returns both
    lists rather than filtering for dashes.

    An unrecognised flag consumes nothing, so its value surfaces as a stray
    positional. That is intentional: the flag assertion fails first, and the
    positional count then corroborates it rather than masking it.

    Args:
        command: The resolved Click command.
        tokens: The argument tokens after the subcommand path.

    Returns:
        ``(flags, positionals)``, with ``--flag=value`` normalised to ``--flag``.
    """
    by_opt: dict[str, Any] = {}
    for param in command.params:
        for opt in (*param.opts, *param.secondary_opts):
            if opt.startswith("-"):
                by_opt[opt] = param

    flags: list[str] = []
    positionals: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            name, separator, _value = token.partition("=")
            flags.append(name)
            param = by_opt.get(name)
            if param is not None and not separator and not getattr(param, "is_flag", False):
                index += 1
        else:
            positionals.append(token)
        index += 1
    return flags, positionals


def _argument_slots(command: Any) -> tuple[int, int]:
    """Count a command's positional parameters.

    Args:
        command: A Click command.

    Returns:
        ``(required, total)`` positional argument counts.
    """
    arguments = [param for param in command.params if not any(opt.startswith("-") for opt in param.opts)]
    required = [param for param in arguments if getattr(param, "required", False)]
    return len(required), len(arguments)


def _inspect(gate: Any, state: FlowState) -> tuple[str, list[str], Any, list[str], list[str], list[str]]:
    """Parse one gate's command against the real CLI.

    Args:
        gate: The gate to interrogate.
        state: The state supplying interpolated values.

    Returns:
        ``(command_string, argv, resolved, path, flags, positionals)``.
    """
    command_string = gate.command(state)
    argv = shlex.split(command_string)
    resolved, path, remaining = _resolve(argv[1:])
    flags, positionals = _split_arguments(resolved, remaining)
    return command_string, argv, resolved, path, flags, positionals


# ===========================================================================
# The generated commands, checked against the parser
# ===========================================================================


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_command_invokes_the_demo_create_executable(gate, state_builder) -> None:
    """The string is pasted into a shell, so the first token names the binary.

    Checked through ``shlex`` rather than ``startswith`` so that a command
    which accidentally quoted its own name -- ``"demo-create lookml" model`` --
    is caught rather than passing a prefix check.
    """
    _command, argv, _resolved, _path, _flags, _positionals = _inspect(gate, state_builder())

    assert argv[0] == _EXECUTABLE


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_subcommand_path_resolves_against_the_real_cli(gate, state_builder) -> None:
    """``lookml model`` must be a command that exists, not one that reads plausibly.

    A misremembered verb -- ``lookml generate``, ``agent provision`` -- is the
    single easiest mistake to make when writing these strings by hand, and it
    is invisible to a test that compares one hand-written string to another.
    """
    command_string = gate.command(state_builder())
    _resolved, path, remaining = _resolve(shlex.split(command_string)[1:])

    assert path, f"No subcommand resolved from {command_string!r}"
    assert not remaining or remaining[0].startswith("-"), (
        f"{remaining[0]!r} in {command_string!r} was not consumed as a subcommand; "
        f"only {path} resolved. Is it a misspelled command name?"
    )


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_resolved_command_is_a_leaf_not_a_group(gate, state_builder) -> None:
    """Stopping at a group means the agent runs a help screen, not a build step.

    ``demo-create lookml`` exits non-zero with ``no_args_is_help``, which an
    orchestrator reads as a failed gate rather than as a malformed instruction.
    """
    command_string, _argv, resolved, path, _flags, _positionals = _inspect(gate, state_builder())

    assert not _is_group(resolved), f"{command_string!r} stops at the group {path}, which runs no work"


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_every_flag_is_declared_on_the_resolved_command(gate, state_builder) -> None:
    """The assertion this whole file exists for.

    Click rejects an undeclared option with exit code 2 before the command body
    runs. From an agent's side that is indistinguishable from a broken install,
    and no amount of pinning strings against other strings can catch it --
    only the parser knows what it accepts.
    """
    command_string, _argv, resolved, path, flags, _positionals = _inspect(gate, state_builder())
    declared = _declared_option_names(resolved)

    undeclared = sorted(set(flags) - declared)
    assert not undeclared, (
        f"{command_string!r} passes {undeclared} to `{' '.join(path)}`, which declares only {sorted(declared)}"
    )


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_no_flag_is_supplied_twice(gate, state_builder) -> None:
    """A repeated option silently keeps the last value on a non-multiple option.

    That is worse than a hard failure: the command runs, against whichever of
    the two values happened to come second, and the report says it succeeded.
    """
    command_string, _argv, _resolved, _path, flags, _positionals = _inspect(gate, state_builder())

    duplicated = sorted({flag for flag in flags if flags.count(flag) > 1})
    assert not duplicated, f"{command_string!r} passes {duplicated} more than once"


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_every_required_option_is_supplied(gate, state_builder) -> None:
    """Omitting a required flag fails at exit 2 just as badly as misspelling one.

    This is the converse assertion, and it is the one that fires when a
    command *gains* a requirement. Today no target declares a required option;
    the day one does, this test is what makes ``gates.py`` learn about it.
    """
    command_string, _argv, resolved, path, flags, _positionals = _inspect(gate, state_builder())

    missing = [
        param.opts[0]
        for param in resolved.params
        if getattr(param, "required", False)
        and any(opt.startswith("-") for opt in param.opts)
        and not (set(param.opts) | set(param.secondary_opts)) & set(flags)
    ]
    assert not missing, f"`{' '.join(path)}` requires {missing}, which {command_string!r} does not supply"


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_positional_arguments_fill_the_declared_slots(gate, state_builder) -> None:
    """A stray bare word is a value that lost its flag.

    It is the shape a quoting bug takes: drop ``--dataset`` and ``retail``
    becomes a positional Click has nowhere to put, so the command fails with a
    message about an unexpected argument rather than about a missing option.
    """
    command_string, _argv, resolved, _path, _flags, positionals = _inspect(gate, state_builder())
    required, total = _argument_slots(resolved)

    assert required <= len(positionals) <= total, (
        f"{command_string!r} supplies {len(positionals)} positional argument(s) "
        f"({positionals}) for a command declaring {required}-{total}"
    )


@pytest.mark.parametrize(("gate", "state_builder"), _CASES)
def test_target_command_accepts_json(gate, state_builder) -> None:
    """An agent appends ``--json`` to whatever ``status`` hands it.

    The whole point of the gate model is that the next command is machine
    drivable; one that could not emit an envelope would break the loop at the
    step after the one this file checks. Run across both states because gate 1
    targets a *different command* in each -- ``data generate`` before Parquet
    exists, ``data upload`` after -- and only one of them would be covered by a
    single-state check.
    """
    _command, _argv, resolved, path, _flags, _positionals = _inspect(gate, state_builder())

    assert "--json" in _declared_option_names(resolved), f"`{' '.join(path)}` cannot emit a JSON envelope"


@pytest.mark.parametrize("gate", GATES, ids=_GATE_IDS)
def test_placeholders_survive_shell_tokenisation(gate) -> None:
    """A placeholder must reach the agent as one token, not as two.

    ``<row count>`` with a space would ``shlex.split`` into two argv entries,
    shifting every following token by one and turning a legible "fill this in"
    into a command that parses as something else entirely.
    """
    command_string, argv, _resolved, _path, _flags, _positionals = _inspect(gate, _unstarted_state())

    for token in argv:
        if token.startswith("<"):
            assert token.endswith(">"), f"{token!r} in {command_string!r} is not a single placeholder token"


# ===========================================================================
# The checker itself
#
# A validity checker that silently passes everything is worse than none, so
# these prove the helpers above can actually see a mistake.
# ===========================================================================


def test_declared_options_are_discovered_for_a_known_command() -> None:
    """Guards against ``_declared_option_names`` returning an empty set.

    If introspection quietly stopped working -- a Typer upgrade moving
    ``params``, say -- every assertion above would become ``set() - set()`` and
    pass forever while checking nothing.
    """
    model, _path, _remaining = _resolve(["lookml", "model"])

    assert {"--looker-project", "--dataset", "--connection", "--gcp-project"} <= _declared_option_names(model)


def test_the_historical_project_rename_would_be_caught() -> None:
    """``--project`` was split into ``--gcp-project`` and ``--looker-project``.

    Pinning that the old spelling is *absent* proves the flag assertion has
    teeth: had ``gates.py`` kept the pre-rename name, the test above would
    fail rather than shrug.
    """
    model, _path, _remaining = _resolve(["lookml", "model"])

    assert "--project" not in _declared_option_names(model)


def test_the_connection_flag_is_still_spelled_as_generated() -> None:
    """``--connection`` changed default this phase; the *name* must not follow.

    A wrong-but-accepted connection is the worst failure mode in the pipeline:
    the model deploys, validates, and then cannot run a single query. Nothing
    downstream notices, so the check has to be here.
    """
    model, _path, _remaining = _resolve(["lookml", "model"])

    assert "--connection" in _declared_option_names(model)


def test_a_nonexistent_subcommand_does_not_resolve() -> None:
    """Proves the resolver reports a bad verb instead of silently stopping short.

    ``lookml generate`` is the plausible-sounding command that does not exist;
    if the walker treated the leftover token as an argument, a misspelled verb
    would sail through every test in this file.
    """
    resolved, path, remaining = _resolve(["lookml", "generate", "--dataset", "retail"])

    assert path == ["lookml"]
    assert remaining[0] == "generate"
    assert _is_group(resolved)


def test_flag_extraction_separates_flags_from_their_values() -> None:
    """The parse must not mistake a value for a flag, or a bool flag for one that eats one.

    ``--fix`` takes no value while ``--gcp-project`` does; getting that
    backwards would silently swallow the next flag and hide a genuine error.
    """
    pre_check, _path, _remaining = _resolve(["pre-check"])

    flags, positionals = _split_arguments(pre_check, ["--fix", "--gcp-project", "acme-analytics"])

    assert flags == ["--fix", "--gcp-project"]
    assert positionals == []
