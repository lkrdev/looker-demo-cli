# Contributing

Practical guide for making your first change to `demo-create`.

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first if you are touching
anything beyond a single command body — it documents the invariants (the
stdout/stderr split, the exit-code table, the layering rule) that a review will
hold you to.

---

## Setup

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone <repo> && cd looker-demo-cli
uv sync --group dev
```

Dev tooling lives in the `[dependency-groups] dev` block of `pyproject.toml`
(`pytest`, `pytest-cov`, `responses`, `ruff`, `mypy`, and the `types-*` stubs).
`--group dev` is what installs it; a bare `uv sync` gives you runtime deps only.

Run the CLI from source without installing it:

```bash
uv run demo-create --help
```

---

## The local gate

Run all four before pushing. These are the exact commands CI runs
(`.github/workflows/test.yml`), so if they pass locally they pass there.

```bash
uv run ruff check .                    # CI adds --output-format=github
uv run ruff format --check .
uv run mypy looker_demo_cli
uv run pytest
```

`ruff format` (no `--check`) fixes formatting in place. `ruff check --fix`
fixes the auto-fixable lint findings.

CI additionally builds the package and verifies the wheel exposes the
entrypoint, which catches packaging regressions (notably the `skills`
force-include) before a release tag:

```bash
uv build
```

### Coverage

CI runs pytest with a hard floor:

```bash
uv run pytest \
  --cov=looker_demo_cli \
  --cov-report=term-missing \
  --cov-fail-under=63
```

> [!IMPORTANT]
> `COVERAGE_FLOOR` is a **ratchet**. Raise it as you add tests; never lower it.
> It currently sits at `63`, deliberately a couple of points below the measured
> figure so an unrelated PR that shifts a branch ratio does not fail on arrival.
> Lowering it to make a red build green is the one change that will always be
> rejected.

---

## Test markers

Declared in `[tool.pytest.ini_options] markers`. `--strict-markers` is on, so an
undeclared marker is an error, not a typo you find later.

| Marker | Meaning |
| --- | --- |
| `unit` | Hermetic: no network, no subprocess, no host dependency |
| `integration` | Needs a provisioned host (`gcloud` / `uv` / `lkr`) or real subprocesses |
| `characterization` | Pins current observable behaviour to detect refactor regressions |

**`integration` is deselected by default.** `addopts` carries
`-m "not integration"`, so a plain `pytest` run is fully hermetic and works in a
clean container. Opt in explicitly:

```bash
uv run pytest -m integration
uv run pytest -m "unit or characterization"
```

Most modules set `pytestmark = pytest.mark.unit` at module level and apply
`characterization` per-test.

---

## Characterization tests: the convention

A test marked `@pytest.mark.characterization` and carrying a `# BUG:` note pins
**known-wrong behaviour on purpose**. It is not an endorsement — it is a tripwire
that makes the eventual fix a visible, intentional edit rather than an accident.

```python
@pytest.mark.characterization
def test_domain_defaults_silently(invoke):
    """Pins that --domain falls back rather than erroring."""
    # BUG: --domain silently defaults to "logistics_analytics" instead of being
    # required, so a typo produces a plausible-looking demo of the wrong domain.
    ...
```

Write the `# BUG:` note so it names **the defect and its consequence**, and the
phase or issue that owns the fix where one is known.

> [!CAUTION]
> If you fix the underlying bug, you must **invert** the test — never delete it.
> Rename it to describe the *correct* behaviour, rewrite the docstring, drop the
> `# BUG:` note, and change the marker to `unit`. Deleting it removes the only
> record that the behaviour was ever considered, and the bug reappears
> unnoticed.

---

## Code style

| Rule | Where enforced |
| --- | --- |
| Line length 120 | `[tool.ruff] line-length = 120` (the formatter, not the linter — `E501` is ignored) |
| Target Python 3.12, `from __future__ import annotations` | `target-version = "py312"`, `UP` ruleset |
| Import order | ruff `I` (isort) |
| Google-style docstrings on every public function and command | Review |

Docstrings are not optional. Every command function documents each of its
options under `Args:` — including `ctx`, `output_json`, and `state_file` — and
every public service function documents its `Args:`, `Returns:`, and `Raises:`.
The `--help` text a user and an agent read comes from the first docstring line,
so it is the interface, not decoration.

**Comments explain WHY, not WHAT.** The codebase is dense with the former, and
that is intentional: a comment saying `# increment the counter` is noise, while
one saying *this must be `default_factory` or a monkeypatched fake is ignored*
is the only thing standing between the next contributor and an afternoon lost.
If you remove a comment that explains a hazard, you own the regression.

Ruff's enabled rulesets are `E, F, I, UP, B, C4, PLE, RUF`, with `E501`, `B008`
(Typer's whole API is a function call in a default) and `RUF012` (pydantic
mutable class attrs) ignored globally.

---

## Adding a new command

Touch these files, in this order.

**1. `looker_demo_cli/commands/<group>.py`** — the command body. New group?
Create the module with its own `typer.Typer`:

```python
foo_app = typer.Typer(
    name="foo",
    help="…",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,  # required — see ARCHITECTURE.md §3
)
```

The body follows a fixed shape:

```python
@foo_app.command(name="bar")
def foo_bar(
    ctx: typer.Context,
    thing: Annotated[str | None, typer.Option("--thing", help="…")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """One-line summary that becomes the --help text.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        thing: …
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)  # must precede any state read
    app_ctx.set_json_mode(output_json)

    resolved = thing or app_ctx.state.thing
    if not resolved:
        raise missing_option("--thing", purpose="…")  # ConfigError, exit 4

    payload = do_the_work(resolved)  # business logic lives in services/
    app_ctx.save_state()

    result = CommandResult.success("foo bar", data={...})

    def render(_: CommandResult) -> None:
        print_success(...)  # stderr only; not called under --json

    return emit(result, json_output=output_json, human_renderer=render)
```

Non-negotiables: `--json` and `StateFileOption` on **every** command; all human
output through `utils.console`; the only `return` is `emit(...)`.

**2. `looker_demo_cli/cli.py`** — register it. Nothing else goes in this file.

```python
app.add_typer(foo_app, name="foo")  # or foo.register(app) for a root command
```

Registration order is `--help` order, and follows the gate sequence rather than
alphabetical.

**3. Business logic → `looker_demo_cli/services/`.** If it calls Looker,
BigQuery, or a subprocess, reach it through the corresponding port on
`AppContext` (`app_ctx.bigquery(...)`, `app_ctx.shell`, `app_ctx.looker_auth()`)
so the command stays testable.

**4. Tests** — `tests/test_cli_<group>.py`, using the `invoke`, `patch_cli`,
`fake_bigquery`, `fake_shell` and `state_file` fixtures from `conftest.py`.
Cover at minimum:

- the success envelope under `--json` (parse `result.stdout`, not
  `result.output`, which interleaves both streams);
- each failure path, asserting `SomeError.exit_code` — never a magic number;
- state persistence, if the command writes any.

**5. If the command advances a gate**, update `looker_demo_cli/gates.py` and let
`tests/test_gates_command_validity.py` verify every flag you emit against the
real Click parser.

---

## Pull requests

- Keep the local gate green.
- Raise `COVERAGE_FLOOR` if your change lifts measured coverage.
- Invert characterization tests you fix; do not delete them.
- Note any new external boundary — if it needs a fourth port, say so explicitly
  in the description, because that is an architectural change.
