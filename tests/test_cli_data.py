"""Tests for the ``demo-create data`` command group.

Two kinds of test live here, and the marker says which is which:

* ``@pytest.mark.characterization`` -- pins behavior that is still wrong or
  surprising today, so a later refactor cannot change it by accident. Each one
  carries a ``# BUG:`` note naming the defect and the phase that owns it.
* plain ``@pytest.mark.unit`` -- asserts a contract Phase 2 deliberately
  introduced. These are not descriptive; they are prescriptive.

Phase 2 rewrote the output and error contract for this group, so a large slice
of this module flipped from the first category to the second:

===========================  ==============================  ====================
Command                      Before                          Now
===========================  ==============================  ====================
``data generate``            no ``--json`` at all            emits the envelope
``data upload``              no ``--json``; bad dir exit 1   envelope; exit 4
``data inspect``             ad-hoc ``{error, exists}``      envelope; exit 5
``data inspect`` (partial)   swallowed errors, exit 0        ``PARTIAL``, exit 5
===========================  ==============================  ====================

Stream discipline
-----------------
Rich output now goes to **stderr** and stdout carries the JSON envelope and
nothing else. In ``CliRunner``, ``result.output`` still interleaves both
streams, so assertions on human text read ``result.output``; assertions on the
machine-readable payload read ``result.stdout`` via the :func:`envelope`
helper. A bare ``json.loads(result.stdout)`` succeeding *is* the assertion that
no banner leaked onto the data stream.

All tests are hermetic: BigQuery is faked via the ``fake_bigquery`` fixture and
synthetic data generation is stubbed via the local ``fake_synth`` fixture
(except for one test that deliberately exercises the real generator to pin its
row-count semantics).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from looker_demo_cli.errors import USAGE_EXIT_CODE, ConfigError, RemoteApiError
from looker_demo_cli.output import ENVELOPE_SCHEMA_VERSION

try:  # pytest's default "prepend" import mode puts tests/ on sys.path.
    from conftest import FakeTable, FakeTableSchemaField
except ImportError:  # pragma: no cover - package-style import mode.
    from tests.conftest import FakeTable, FakeTableSchemaField

# Only `unit` is module-wide. `characterization` is applied per-test, because
# it now means something specific: "this pins a defect that is still present".
pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


class FakeSynthesizer:
    """Records calls to the two synthetic-data entry points the CLI uses.

    ``data generate`` calls ``create_dynamic_blueprint_from_name(domain)``,
    mutates the returned blueprint's fact entities, and hands the blueprint to
    ``generate_domain_dataset(target=..., output_dir=...)``. Stubbing both lets
    the tests assert the CLI *wiring* (which domain, which output dir, which row
    count) without paying for real dataframe synthesis.

    Attributes:
        table_names: Table names the fake generator will report.
        write_files: Whether to create a ``<table>.parquet`` file per table.
        blueprint_calls: Domains passed to ``create_dynamic_blueprint_from_name``.
        generate_calls: ``(blueprint, output_dir)`` pairs passed to the generator.
    """

    def __init__(self) -> None:
        self.table_names: list[str] = ["dim_things", "fct_events"]
        self.write_files: bool = True
        self.blueprint_calls: list[str] = []
        self.generate_calls: list[tuple[Any, Path]] = []

    def create_blueprint(self, domain_name: str) -> Any:
        self.blueprint_calls.append(domain_name)
        return SimpleNamespace(
            domain_name=domain_name,
            description=f"fake blueprint for {domain_name}",
            entities=[
                SimpleNamespace(table_name="dim_things", table_type="dimension", row_count=1000),
                SimpleNamespace(table_name="fct_events", table_type="fact", row_count=5000),
            ],
        )

    def generate(self, target: Any, output_dir: Path, micro_sample_only: bool = False) -> list[Any]:
        self.generate_calls.append((target, Path(output_dir)))
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        if self.write_files:
            for name in self.table_names:
                (Path(output_dir) / f"{name}.parquet").write_bytes(b"PAR1-fake")
        return [SimpleNamespace(table_name=n) for n in self.table_names]

    @property
    def recorded_row_counts(self) -> dict[str, int]:
        """Row counts on the blueprint as it was handed to the generator."""
        blueprint, _ = self.generate_calls[-1]
        return {e.table_name: e.row_count for e in blueprint.entities}


@pytest.fixture
def fake_synth(patch_cli) -> FakeSynthesizer:
    """Install :class:`FakeSynthesizer` over the CLI's data-synthesis imports.

    Belongs in ``conftest.py`` eventually -- every command that synthesizes data
    (``data generate``, ``run``) needs it.
    """
    fake = FakeSynthesizer()
    patch_cli("create_dynamic_blueprint_from_name", fake.create_blueprint)
    patch_cli("generate_domain_dataset", fake.generate)
    return fake


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` so default output dirs land in ``tmp_path``.

    ``data generate`` defaults ``--output-dir`` to
    ``~/scratch/demo_create/<dataset-or-domain>``; without this the test suite
    would write into the developer's real home directory.

    Belongs in ``conftest.py``: several commands default paths off ``Path.home()``.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def read_state(cwd: Path) -> dict[str, Any]:
    """Parse the ``.demo-state.json`` written into ``cwd``."""
    path = cwd / ".demo-state.json"
    assert path.exists(), f"expected {path} to exist; dir contains {list(cwd.iterdir())}"
    return json.loads(path.read_text(encoding="utf-8"))


def envelope(result: Any) -> dict[str, Any]:
    """Parse the JSON envelope a ``--json`` invocation wrote to stdout.

    Deliberately parses ``result.stdout`` rather than ``result.output``: the
    Phase 2 contract is that stdout carries the envelope *and nothing else*,
    so a bare ``json.loads`` succeeding is itself the assertion that no Rich
    output leaked onto the machine-readable stream.
    """
    return json.loads(result.stdout)


def default_gcp_project() -> str:
    """The import-time ``DEFAULT_GCP_PROJECT`` the CLI compiled its flags with.

    It is ``os.getenv("GOOGLE_CLOUD_PROJECT", "")`` evaluated at import time, so
    it is normally ``""`` in a hermetic run but may be non-empty on a developer
    machine that imported the module before the env-stripping fixture ran.
    Tests compare against this value rather than hard-coding ``""``.
    """
    from looker_demo_cli.commands.data import DEFAULT_GCP_PROJECT

    return DEFAULT_GCP_PROJECT


# ===========================================================================
# data generate
# ===========================================================================


def test_generate_happy_path_writes_parquet_and_state(invoke, fake_synth, isolated_cwd, tmp_path):
    """The baseline contract: files on disk and pipeline state on disk."""
    out = tmp_path / "parquet_out"
    result = invoke(["data", "generate", "--domain", "retail_ops", "--row-count", "25", "--output-dir", str(out)])

    assert result.exit_code == 0
    assert out.is_dir()
    assert sorted(p.name for p in out.glob("*.parquet")) == ["dim_things.parquet", "fct_events.parquet"]

    state = read_state(isolated_cwd)
    assert state["domain_name"] == "retail_ops"
    assert state["bq_dataset_id"] == "retail_ops"
    assert state["generated_tables"] == ["dim_things", "fct_events"]
    assert Path(state["generated_parquet_dir"]) == out
    # No upload was requested, so the dataset is still not marked as existing.
    assert state["dataset_exists"] is False


def test_generate_writes_nothing_to_stdout_without_json(invoke, fake_synth, tmp_path):
    """Progress narration must never contaminate the data stream.

    This is the load-bearing half of the Phase 2 stream split: if any Rich
    output reached stdout here, then ``--json`` piped into ``jq`` would break
    the moment that code path also ran in JSON mode.
    """
    result = invoke(["data", "generate", "--domain", "d", "--output-dir", str(tmp_path / "o")])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""
    # The narration is not lost -- it went to stderr, which `output` includes.
    assert "Generated 2 tables" in result.output


def test_generate_wires_domain_and_output_dir_through_to_generator(invoke, fake_synth, tmp_path):
    """Options must reach the synthesizer unmangled, not just be accepted."""
    out = tmp_path / "wired"
    result = invoke(["data", "generate", "--domain", "supply_chain", "--output-dir", str(out)])

    assert result.exit_code == 0
    assert fake_synth.blueprint_calls == ["supply_chain"]
    assert len(fake_synth.generate_calls) == 1
    _blueprint, called_dir = fake_synth.generate_calls[0]
    assert called_dir == out


@pytest.mark.characterization
def test_generate_row_count_is_applied_to_fact_entities_only(invoke, fake_synth, tmp_path):
    """``--row-count`` does not mean what its name says."""
    result = invoke(["data", "generate", "--domain", "d", "--row-count", "77", "--output-dir", str(tmp_path / "o")])

    assert result.exit_code == 0
    # BUG: --row-count is applied only to entities whose table_type == "fact"
    # (cli.py, data_generate). Dimension tables silently keep the blueprint's
    # hard-coded 1000 rows, so `--row-count 5` still synthesizes a 1000-row
    # dimension. Untouched by Phase 2, which only reshaped output and errors.
    assert fake_synth.recorded_row_counts == {"dim_things": 1000, "fct_events": 77}


@pytest.mark.characterization
def test_generate_defaults_domain_to_logistics_analytics_silently(invoke, fake_synth, isolated_cwd, tmp_path):
    """Forgetting ``--domain`` produces a full dataset rather than a complaint."""
    result = invoke(["data", "generate", "--output-dir", str(tmp_path / "o")])

    assert result.exit_code == 0
    # BUG: --domain silently defaults to "logistics_analytics" instead of being
    # required or prompted for. A user who forgets the flag gets a
    # fully-synthesized logistics dataset with no warning at all. Note this is
    # now checked against `output` (stdout + stderr): the warning is absent from
    # *both* streams, not merely absent from the JSON one.
    assert fake_synth.blueprint_calls == ["logistics_analytics"]
    state = read_state(isolated_cwd)
    assert state["domain_name"] == "logistics_analytics"
    assert state["bq_dataset_id"] == "logistics_analytics"
    assert "warn" not in result.output.lower()


@pytest.mark.characterization
def test_generate_default_output_dir_is_under_home_scratch(invoke, fake_synth, fake_home, isolated_cwd):
    """Generated artifacts escape the project the user is standing in."""
    result = invoke(["data", "generate", "--domain", "widgets"])

    assert result.exit_code == 0
    expected = fake_home / "scratch" / "demo_create" / "widgets"
    assert expected.is_dir()
    # BUG(scoping): the default output directory is the user's *home* directory,
    # not the current working directory, so generated artifacts land outside the
    # project the user is working in.
    assert Path(read_state(isolated_cwd)["generated_parquet_dir"]) == expected


@pytest.mark.characterization
def test_generate_dataset_option_names_both_the_dataset_and_the_output_dir(invoke, fake_synth, fake_home, isolated_cwd):
    """One flag quietly controls two unrelated things."""
    result = invoke(["data", "generate", "--domain", "widgets", "--dataset", "custom_ds"])

    assert result.exit_code == 0
    state = read_state(isolated_cwd)
    assert state["bq_dataset_id"] == "custom_ds"
    # domain_name still tracks --domain even though the dataset id does not.
    assert state["domain_name"] == "widgets"
    # BUG: the default output dir keys off `dataset or domain`, so --dataset --
    # nominally a *BigQuery* concern -- silently relocates where local Parquet
    # files are written.
    assert Path(state["generated_parquet_dir"]) == fake_home / "scratch" / "demo_create" / "custom_ds"


def test_generate_gcp_project_falls_back_to_state_file(invoke, fake_synth, state_file, isolated_cwd, tmp_path):
    """Prior pipeline state supplies the project when the flag is omitted."""
    state_file(gcp_project_id="project-from-state", bq_dataset_id="ignored_ds")
    result = invoke(["data", "generate", "--domain", "d", "--output-dir", str(tmp_path / "o")])

    assert result.exit_code == 0
    expected = default_gcp_project() or "project-from-state"
    assert read_state(isolated_cwd)["gcp_project_id"] == expected


@pytest.mark.characterization
def test_generate_ignores_domain_recorded_in_state_file(invoke, fake_synth, state_file, isolated_cwd, tmp_path):
    """Re-running ``data generate`` forgets which domain you picked."""
    state_file(domain_name="previously_chosen_domain", bq_dataset_id="previously_chosen_domain")
    result = invoke(["data", "generate", "--output-dir", str(tmp_path / "o")])

    assert result.exit_code == 0
    # BUG: unlike every other option in this group, --domain has NO state
    # fallback. A prior `data generate --domain x` is silently overwritten by
    # the "logistics_analytics" default on the next invocation.
    assert fake_synth.blueprint_calls == ["logistics_analytics"]
    assert read_state(isolated_cwd)["domain_name"] == "logistics_analytics"


def test_generate_with_upload_loads_every_table_into_bigquery(
    invoke, fake_synth, fake_bigquery, isolated_cwd, tmp_path
):
    """``--upload`` collapses generate+upload into one step, state included."""
    result = invoke(
        [
            "data",
            "generate",
            "--domain",
            "freight",
            "--output-dir",
            str(tmp_path / "o"),
            "--upload",
            "--gcp-project",
            "proj-x",
            "--dataset",
            "freight_ds",
        ]
    )

    assert result.exit_code == 0
    assert fake_bigquery.datasets == {"freight_ds": ["dim_things", "fct_events"]}
    state = read_state(isolated_cwd)
    assert state["dataset_exists"] is True
    assert state["bq_dataset_id"] == "freight_ds"
    assert state["gcp_project_id"] == "proj-x"
    assert state["generated_tables"] == ["dim_things", "fct_events"]


@pytest.mark.characterization
def test_generate_with_upload_silently_skips_tables_with_no_parquet_file(
    invoke, fake_synth, fake_bigquery, isolated_cwd, tmp_path
):
    """A load that uploaded nothing still reports unqualified success."""
    fake_synth.write_files = False
    result = invoke(
        ["data", "generate", "--domain", "d", "--output-dir", str(tmp_path / "o"), "--upload", "--dataset", "ds"]
    )

    assert result.exit_code == 0
    # BUG: tables whose Parquet file is missing are skipped without any warning,
    # yet the run still reports SUCCESS AND marks the dataset as existing -- so
    # an empty dataset looks like a successful load downstream. Phase 2 gave
    # this command an envelope but not the `PARTIAL` status it deserves here;
    # compare `data inspect`, which was fixed.
    assert fake_bigquery.datasets == {"ds": []}
    state = read_state(isolated_cwd)
    assert state["dataset_exists"] is True
    assert state["generated_tables"] == ["dim_things", "fct_events"]


def test_generate_json_emits_the_envelope(invoke, fake_synth, isolated_cwd, tmp_path):
    """``data generate`` gained ``--json`` in Phase 2; it previously had none.

    Before this, an orchestrator had to scrape a Rich success line to learn
    which tables were produced and where they landed.
    """
    out = tmp_path / "json_out"
    result = invoke(
        ["data", "generate", "--domain", "retail_ops", "--row-count", "25", "--output-dir", str(out), "--json"]
    )

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["command"] == "data generate"
    assert payload["status"] == "SUCCESS"
    assert payload["errors"] == []
    assert set(payload["data"]) == {
        "domain",
        "row_count",
        "output_dir",
        "dataset",
        "gcp_project",
        "tables",
        "uploaded",
        "loaded_rows",
        "state_file",
    }
    assert payload["data"]["domain"] == "retail_ops"
    assert payload["data"]["row_count"] == 25
    assert Path(payload["data"]["output_dir"]) == out
    assert payload["data"]["dataset"] == "retail_ops"
    assert payload["data"]["tables"] == ["dim_things", "fct_events"]
    assert payload["data"]["uploaded"] is False
    assert payload["data"]["loaded_rows"] == {}
    assert Path(payload["data"]["state_file"]) == isolated_cwd / ".demo-state.json"


def test_generate_json_without_upload_points_at_data_upload(invoke, fake_synth, tmp_path):
    """The envelope tells the orchestrator the literal next command to run.

    This is what lets a caller walk Gate 1 -> Gate 2 without re-reading a
    prompt file, and it carries the human-confirmation flag so an autonomous
    agent knows to stop and ask.
    """
    out = tmp_path / "next"
    result = invoke(["data", "generate", "--domain", "widgets", "--output-dir", str(out), "--json"])

    assert result.exit_code == 0
    actions = envelope(result)["next_actions"]
    assert len(actions) == 1
    assert actions[0]["description"] == "Load the generated Parquet tables into BigQuery"
    assert actions[0]["command"] == f"demo-create data upload --parquet-dir {out} --dataset widgets"
    assert actions[0]["gate"] == 1
    assert actions[0]["requires_human_confirmation"] is True


def test_generate_json_with_upload_suggests_no_follow_up_upload(invoke, fake_synth, fake_bigquery, tmp_path):
    """``--upload`` already did the follow-up, so suggesting it would loop."""
    result = invoke(
        [
            "data",
            "generate",
            "--domain",
            "freight",
            "--output-dir",
            str(tmp_path / "o"),
            "--upload",
            "--dataset",
            "freight_ds",
            "--json",
        ]
    )

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["next_actions"] == []
    assert payload["data"]["uploaded"] is True
    # Row counts come from the loader, keyed by table, not just a total.
    assert payload["data"]["loaded_rows"] == {"dim_things": 42, "fct_events": 42}


@pytest.mark.characterization
def test_generate_rejects_readme_documented_scale_flag(invoke, fake_synth):
    """The documented invocation does not parse."""
    result = invoke(["data", "generate", "--domain", "d", "--scale", "small"])

    # BUG(docs): the README documents `--scale`, but the command only accepts
    # `--row-count`. The documented flag is a usage error. Exit 2 is Click's
    # reserved usage code and is deliberately never reused by errors.py.
    assert result.exit_code == 2
    assert fake_synth.blueprint_calls == []


def test_generate_help_lists_current_option_names(invoke):
    """Guards against an option being renamed without the docs following."""
    result = invoke(["data", "generate", "--help"])

    assert result.exit_code == 0
    for flag in ("--domain", "--row-count", "--output-dir", "--upload", "--gcp-project", "--dataset", "--json"):
        assert flag in result.stdout
    assert "--scale" not in result.stdout
    # The GCP flag is `--gcp-project`; a bare `--project` does not exist here.
    assert "--project " not in result.stdout


def test_generate_real_synthesizer_row_counts_and_table_names(invoke, isolated_cwd, tmp_path):
    """One end-to-end pass through the *real* generator (no synth stub).

    The stubbed tests above prove the CLI's wiring; this proves the wiring is
    connected to something that actually works.
    """
    pd = pytest.importorskip("pandas")
    out = tmp_path / "real"
    result = invoke(["data", "generate", "--domain", "Trucking IoT", "--row-count", "7", "--output-dir", str(out)])

    assert result.exit_code == 0
    # The domain string is normalized (lowercased, spaces -> underscores) before
    # being embedded in the generated table names (schema_generator.py:201-209).
    assert sorted(p.name for p in out.glob("*.parquet")) == [
        "dim_trucking_iot_entities.parquet",
        "fct_trucking_iot_events.parquet",
    ]
    dim = pd.read_parquet(out / "dim_trucking_iot_entities.parquet")
    fct = pd.read_parquet(out / "fct_trucking_iot_events.parquet")
    # BUG: --row-count only reaches fact tables; the dimension keeps the
    # blueprint's hard-coded 1000 rows (schema_generator.py:212).
    assert len(dim) == 1000
    assert len(fct) == 7

    state = read_state(isolated_cwd)
    # The un-normalized domain string is what gets persisted as the dataset id,
    # even though the generated table names use the normalized form.
    assert state["domain_name"] == "Trucking IoT"
    assert state["bq_dataset_id"] == "Trucking IoT"
    assert state["generated_tables"] == ["dim_trucking_iot_entities", "fct_trucking_iot_events"]


# ===========================================================================
# data upload
# ===========================================================================


@pytest.mark.characterization
def test_upload_happy_path_loads_all_parquet_files(invoke, fake_bigquery, sample_parquet_dir, isolated_cwd):
    """Every ``*.parquet`` file becomes a table, and the state file records it."""
    result = invoke(
        [
            "data",
            "upload",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--dataset",
            "retail",
            "--gcp-project",
            "proj-1",
            "--location",
            "EU",
        ]
    )

    assert result.exit_code == 0
    # Files are loaded in sorted filename order.
    assert fake_bigquery.datasets == {"retail": ["dim_products", "dim_users", "fct_orders"]}

    state = read_state(isolated_cwd)
    assert state["generated_tables"] == ["dim_products", "dim_users", "fct_orders"]
    assert state["dataset_exists"] is True
    assert state["bq_dataset_id"] == "retail"
    assert state["gcp_project_id"] == "proj-1"
    assert state["gcp_location"] == "EU"
    # BUG: --parquet-dir is NOT persisted; only `data generate` sets
    # generated_parquet_dir, so a generate-free upload leaves it null and a
    # later command cannot find the files that were just loaded. Owned by
    # Phase 5, which consolidates state handling.
    assert state["generated_parquet_dir"] is None


def test_upload_reports_row_counts_from_the_loader(invoke, fake_bigquery, sample_parquet_dir):
    """The human stream still narrates per-table row counts, on stderr."""
    fake_bigquery.rows_per_load = 1234
    result = invoke(["data", "upload", "--parquet-dir", str(sample_parquet_dir), "--dataset", "retail"])

    assert result.exit_code == 0
    # Row counts are thousands-separated in the success line. Asserted against
    # `output` because the line is now written to stderr, not stdout.
    assert "1,234" in result.output
    assert result.stdout.strip() == ""


def test_upload_without_dir_or_state_is_a_config_error(invoke, fake_bigquery, isolated_cwd):
    """An unresolvable ``--parquet-dir`` is a configuration failure, not a generic one.

    Previously this exited 1, indistinguishable from a crash. Exit 4 lets a
    caller branch on "the inputs are wrong, do not retry" without parsing prose.
    """
    result = invoke(["data", "upload", "--dataset", "retail"])

    assert result.exit_code == ConfigError.exit_code
    assert "No Parquet directory found" in result.output
    assert not (isolated_cwd / ".demo-state.json").exists()
    assert fake_bigquery.datasets == {}


def test_upload_with_nonexistent_dir_is_a_config_error(invoke, fake_bigquery, tmp_path, isolated_cwd):
    """A path that does not exist reports the path it could not find.

    The message is still shared with the "nothing configured" case, but the
    structured error now carries the offending path in ``details``, so the
    ambiguity is resolvable by a machine even though the prose is not.
    """
    missing = tmp_path / "nope"
    result = invoke(["data", "upload", "--parquet-dir", str(missing), "--dataset", "retail", "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "CONFIG_ERROR"
    assert payload["errors"][0]["details"] == {"parquet_dir": str(missing)}
    assert payload["errors"][0]["remediation"]
    assert not (isolated_cwd / ".demo-state.json").exists()


def test_upload_with_empty_dir_fails_without_creating_the_dataset(invoke, fake_bigquery, tmp_path, isolated_cwd):
    """An empty directory fails cleanly and leaves nothing behind in BigQuery.

    ``ensure_dataset`` *creates* the dataset, and it used to run before the CLI
    checked that any Parquet files existed. A rejected upload therefore left an
    empty orphan dataset on the warehouse -- which a later ``data inspect``
    reports as an existing-but-empty dataset, i.e. indistinguishable from a load
    that succeeded and produced nothing. Every input is now validated before the
    BigQuery client is constructed at all.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    result = invoke(["data", "upload", "--parquet-dir", str(empty), "--dataset", "orphan_ds"])

    assert result.exit_code == ConfigError.exit_code
    assert "No .parquet files found" in result.output
    # The guarantee is the absence of the side effect: the fake records a
    # dataset the moment ensure_dataset is called, so an empty mapping proves
    # the command failed before touching BigQuery -- not merely that it loaded
    # no tables into a dataset it had already created.
    assert fake_bigquery.datasets == {}
    assert not (isolated_cwd / ".demo-state.json").exists()


def test_upload_falls_back_to_state_for_parquet_dir_and_dataset(
    invoke, fake_bigquery, sample_parquet_dir, state_file, isolated_cwd
):
    """A bare ``data upload`` picks up where ``data generate`` left off."""
    state_file(
        generated_parquet_dir=str(sample_parquet_dir),
        bq_dataset_id="dataset_from_state",
        gcp_project_id="project-from-state",
    )
    result = invoke(["data", "upload"])

    assert result.exit_code == 0
    assert list(fake_bigquery.datasets) == ["dataset_from_state"]
    state = read_state(isolated_cwd)
    assert state["bq_dataset_id"] == "dataset_from_state"
    assert state["gcp_project_id"] == (default_gcp_project() or "project-from-state")
    # --location has a non-None default, so it always overwrites the state value.
    assert state["gcp_location"] == "US"


def test_upload_without_dataset_is_a_config_error_naming_the_flag(
    invoke, fake_bigquery, sample_parquet_dir, isolated_cwd
):
    """With nothing to go on, the command refuses rather than inventing a destination.

    ``FlowState.bq_dataset_id`` used to default to ``"logistics_analytics"``, so
    a bare upload silently loaded an unrelated demo's tables into a logistics
    dataset -- and, because the default was truthy, the ``or`` chain's final arm
    was unreachable. The field is now ``None`` and the miss is reported against
    the flag the caller would actually type.
    """
    result = invoke(["data", "upload", "--parquet-dir", str(sample_parquet_dir), "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "CONFIG_ERROR"
    assert payload["errors"][0]["details"] == {"option": "--dataset"}
    assert "demo-create data generate" in payload["errors"][0]["remediation"]
    # Refusing early means refusing before any warehouse mutation.
    assert fake_bigquery.datasets == {}
    assert not (isolated_cwd / ".demo-state.json").exists()


def test_upload_does_not_duplicate_tables_already_in_state(
    invoke, fake_bigquery, sample_parquet_dir, state_file, isolated_cwd
):
    """Re-uploading must not grow ``generated_tables`` without bound."""
    state_file(generated_tables=["dim_users", "already_there"], bq_dataset_id="retail")
    result = invoke(["data", "upload", "--parquet-dir", str(sample_parquet_dir)])

    assert result.exit_code == 0
    # Existing entries are preserved and only new table names are appended;
    # ordering is "state order, then load order".
    assert read_state(isolated_cwd)["generated_tables"] == [
        "dim_users",
        "already_there",
        "dim_products",
        "fct_orders",
    ]


def test_upload_ignores_non_parquet_files_in_the_directory(invoke, fake_bigquery, tmp_path, isolated_cwd):
    """A scratch directory usually has notes and CSVs in it too."""
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "orders.parquet").write_bytes(b"PAR1-fake")
    (mixed / "notes.txt").write_text("ignore me", encoding="utf-8")
    (mixed / "data.csv").write_text("a,b", encoding="utf-8")

    result = invoke(["data", "upload", "--parquet-dir", str(mixed), "--dataset", "ds"])

    assert result.exit_code == 0
    assert fake_bigquery.datasets == {"ds": ["orders"]}
    assert read_state(isolated_cwd)["generated_tables"] == ["orders"]


def test_upload_json_emits_the_envelope(invoke, fake_bigquery, sample_parquet_dir, isolated_cwd):
    """``data upload`` gained ``--json`` in Phase 2; it previously had none.

    ``total_rows`` is precomputed so a caller does not have to sum the
    per-table map itself just to decide whether anything landed.
    """
    result = invoke(
        [
            "data",
            "upload",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--dataset",
            "retail",
            "--gcp-project",
            "proj-1",
            "--location",
            "EU",
            "--json",
        ]
    )

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert payload["command"] == "data upload"
    assert payload["status"] == "SUCCESS"
    assert set(payload["data"]) == {
        "gcp_project",
        "dataset",
        "location",
        "parquet_dir",
        "loaded_rows",
        "total_rows",
        "state_file",
    }
    assert payload["data"]["gcp_project"] == "proj-1"
    assert payload["data"]["dataset"] == "retail"
    assert payload["data"]["location"] == "EU"
    assert Path(payload["data"]["parquet_dir"]) == sample_parquet_dir
    assert payload["data"]["loaded_rows"] == {"dim_products": 42, "dim_users": 42, "fct_orders": 42}
    assert payload["data"]["total_rows"] == 126
    assert Path(payload["data"]["state_file"]) == isolated_cwd / ".demo-state.json"


def test_upload_json_points_at_lookml_model(invoke, fake_bigquery, sample_parquet_dir):
    """A loaded warehouse is the precondition for Gate 2, so say so."""
    result = invoke(
        [
            "data",
            "upload",
            "--parquet-dir",
            str(sample_parquet_dir),
            "--dataset",
            "retail",
            "--gcp-project",
            "proj-1",
            "--json",
        ]
    )

    assert result.exit_code == 0
    actions = envelope(result)["next_actions"]
    assert len(actions) == 1
    assert actions[0]["command"] == "demo-create lookml model --dataset retail --gcp-project proj-1"
    assert actions[0]["gate"] == 2
    assert actions[0]["requires_human_confirmation"] is True


def test_upload_rejects_the_ambiguous_project_flag(invoke, fake_bigquery, sample_parquet_dir):
    """``data upload`` accepts ``--gcp-project`` only, never a bare ``--project``.

    This began as a doc-drift pin: the README documented
    ``demo-create data upload --project <id>``, which was a usage error, so the
    documented invocation could not work. Phase 3 corrected the README, AGENTS.md,
    and the skill prompts to ``--gcp-project``.

    The test is kept, with its job inverted. ``--project`` is now deliberately
    reserved: Phase 3 renamed the *Looker* project flag to ``--looker-project``
    precisely so that no command anywhere accepts an unqualified ``--project``
    whose cloud you have to guess. Should someone reintroduce it here as a
    convenience alias, this fails.
    """
    result = invoke(["data", "upload", "--parquet-dir", str(sample_parquet_dir), "--project", "proj-1"])

    # Click's usage-error code, which is reserved and distinct from every
    # DemoCreateError exit code.
    assert result.exit_code == USAGE_EXIT_CODE
    assert fake_bigquery.datasets == {}


def test_upload_help_lists_current_option_names(invoke):
    """Guards against an option being renamed without the docs following."""
    result = invoke(["data", "upload", "--help"])

    assert result.exit_code == 0
    for flag in ("--parquet-dir", "--dataset", "--gcp-project", "--location", "--json"):
        assert flag in result.stdout


# ===========================================================================
# data inspect
# ===========================================================================


def _register_tables(fake_bigquery, dataset_id: str, tables: dict[str, FakeTable]) -> None:
    fake_bigquery.datasets = {dataset_id: list(tables)}
    fake_bigquery.tables = dict(tables)


def test_inspect_json_happy_path_shape(invoke, fake_bigquery, isolated_cwd):
    """The inspection payload now lives under ``data``, inside the envelope.

    Callers that previously read the bare object need to reach one level
    deeper, and gain ``table_count`` plus a status they can trust.
    """
    _register_tables(
        fake_bigquery,
        "retail",
        {
            "dim_users": FakeTable(
                "dim_users",
                [FakeTableSchemaField("user_id", "STRING", "REQUIRED"), FakeTableSchemaField("age", "INT64")],
                num_rows=3,
            )
        },
    )

    result = invoke(["data", "inspect", "--dataset", "retail", "--gcp-project", "proj-1", "--location", "EU", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["command"] == "data inspect"
    assert payload["status"] == "SUCCESS"
    assert payload["errors"] == []
    assert set(payload["data"]) == {"project_id", "dataset_id", "location", "table_count", "tables"}
    assert payload["data"]["project_id"] == "proj-1"
    assert payload["data"]["dataset_id"] == "retail"
    assert payload["data"]["location"] == "EU"
    assert payload["data"]["table_count"] == 1
    assert payload["data"]["tables"] == [
        {
            "table_id": "dim_users",
            "table_type": "TABLE",
            "column_count": 2,
            "num_rows": 3,
            "columns": [
                {"name": "user_id", "field_type": "STRING", "mode": "REQUIRED"},
                {"name": "age", "field_type": "INT64", "mode": "NULLABLE"},
            ],
        }
    ]
    # inspect is read-only: it must not write a state file.
    assert not (isolated_cwd / ".demo-state.json").exists()


def test_inspect_json_per_table_failure_is_partial_and_exits_non_zero(invoke, fake_bigquery):
    """A half-readable dataset must not look healthy to ``&&``.

    Previously a per-table metadata failure was swallowed into
    ``{"table_id": ..., "error": ...}`` and the command exited 0, so a caller
    checking only the exit code would proceed to model a dataset it could not
    fully read. It is now ``PARTIAL``, which ``FAILURE_STATUSES`` maps to a
    non-zero exit.
    """
    # "ghost" is listed in the dataset but has no registered metadata, so
    # client.get_table() raises.
    fake_bigquery.datasets = {"retail": ["dim_users", "ghost"]}
    fake_bigquery.tables = {"dim_users": FakeTable("dim_users", [FakeTableSchemaField("user_id")], num_rows=1)}

    result = invoke(["data", "inspect", "--dataset", "retail", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "PARTIAL"
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert payload["errors"][0]["message"] == "Failed to read metadata for 1 table(s): ghost."
    assert payload["errors"][0]["details"] == {"tables": ["ghost"]}
    assert payload["errors"][0]["remediation"]
    # The readable tables are still reported: a partial result is not an empty
    # one, and the caller may well be able to work with what came back.
    assert payload["data"]["table_count"] == 2
    assert payload["data"]["tables"][0]["table_id"] == "dim_users"
    failed = payload["data"]["tables"][1]
    assert set(failed) == {"table_id", "error"}
    assert failed["table_id"] == "ghost"
    assert "not registered" in failed["error"]


def test_inspect_partial_agrees_between_json_and_the_rich_table(invoke, fake_bigquery):
    """The two renderings are built from one collection pass, so they cannot diverge.

    They previously walked the table list independently, which meant the Rich
    table and the JSON payload could describe different datasets if a
    transient failure hit only one of the two passes.
    """
    fake_bigquery.datasets = {"retail": ["dim_users", "ghost"]}
    fake_bigquery.tables = {"dim_users": FakeTable("dim_users", [FakeTableSchemaField("user_id")], num_rows=1)}

    human = invoke(["data", "inspect", "--dataset", "retail"])
    machine = invoke(["data", "inspect", "--dataset", "retail", "--json"])

    assert human.exit_code == RemoteApiError.exit_code
    assert machine.exit_code == RemoteApiError.exit_code
    payload = envelope(machine)
    for entry in payload["data"]["tables"]:
        assert entry["table_id"] in human.output
    # The unreadable table is flagged in both: "UNKNOWN" in the table body, and
    # the same error message the envelope carries printed beneath it.
    assert "UNKNOWN" in human.output
    assert payload["errors"][0]["message"] in human.output


def test_inspect_missing_dataset_is_a_remote_api_error(invoke, fake_bigquery):
    """A nonexistent dataset is a remote lookup failure: exit 5, not a bare 1."""
    result = invoke(["data", "inspect", "--dataset", "ghost_ds", "--gcp-project", "proj-1"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "does not exist" in result.output


def test_inspect_missing_dataset_json_uses_the_standard_envelope(invoke, fake_bigquery):
    """The old bespoke ``{error, exists}`` payload is gone.

    That shape shared no keys with the success payload, so a consumer needed
    two parsers. The context it carried (project, dataset, ``exists``) survives
    inside ``errors[0].details``.
    """
    result = invoke(["data", "inspect", "--dataset", "ghost_ds", "--gcp-project", "proj-1", "--json"])

    assert result.exit_code == RemoteApiError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "REMOTE_API_ERROR"
    assert payload["errors"][0]["message"] == "Dataset `proj-1.ghost_ds` does not exist."
    assert payload["errors"][0]["details"] == {"gcp_project": "proj-1", "dataset": "ghost_ds", "exists": False}


def test_inspect_json_empty_dataset_is_a_success_with_a_warning(invoke, fake_bigquery):
    """An existing-but-empty dataset is not a failure, but it is worth saying.

    ``warnings`` exists precisely for this: something the caller should know
    that does not justify a non-zero exit.
    """
    fake_bigquery.datasets = {"empty_ds": []}
    result = invoke(["data", "inspect", "--dataset", "empty_ds", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["status"] == "SUCCESS"
    assert payload["data"]["tables"] == []
    assert payload["data"]["table_count"] == 0
    assert payload["data"]["dataset_id"] == "empty_ds"
    assert payload["warnings"] == ["Dataset `empty_ds` exists but has no tables."]


@pytest.mark.characterization
def test_inspect_json_defaults_location_to_us(invoke, fake_bigquery):
    """The reported location is an echo, not a fact."""
    fake_bigquery.datasets = {"ds": []}
    result = invoke(["data", "inspect", "--dataset", "ds", "--json"])

    assert result.exit_code == 0
    # BUG: the reported location is simply the echoed --location default; it is
    # never read back from BigQuery, so inspecting an EU dataset without passing
    # --location reports "US".
    assert envelope(result)["data"]["location"] == "US"


def test_inspect_renders_table_report(invoke, fake_bigquery):
    """The human path keeps its Rich table -- on stderr, and only without ``--json``."""
    _register_tables(
        fake_bigquery,
        "retail",
        {
            "dim_users": FakeTable("dim_users", [FakeTableSchemaField("user_id")], num_rows=1500),
            "fct_orders": FakeTable("fct_orders", [FakeTableSchemaField("order_id")], num_rows=2),
        },
    )

    result = invoke(["data", "inspect", "--dataset", "retail", "--gcp-project", "proj-1"])

    assert result.exit_code == 0
    assert "dim_users" in result.output
    assert "fct_orders" in result.output
    # Row counts are thousands-separated in the Rich table.
    assert "1,500" in result.output
    # Nothing reached stdout, because --json was not passed.
    assert result.stdout.strip() == ""


def test_inspect_renders_unknown_row_for_unreadable_table(invoke, fake_bigquery):
    """The human path degrades gracefully but no longer lies about the outcome.

    It still shows a ``UNKNOWN / ? / ?`` row so the operator sees the table
    exists, but the exception text is now printed and the process exits 5
    instead of silently succeeding.
    """
    fake_bigquery.datasets = {"retail": ["ghost"]}
    fake_bigquery.tables = {}

    result = invoke(["data", "inspect", "--dataset", "retail"])

    assert result.exit_code == RemoteApiError.exit_code
    assert "UNKNOWN" in result.output
    assert "Failed to read metadata for 1 table(s): ghost." in result.output


def test_inspect_empty_dataset_warns_and_exits_0(invoke, fake_bigquery):
    """An empty dataset warns on the human stream and still exits 0."""
    fake_bigquery.datasets = {"empty_ds": []}
    result = invoke(["data", "inspect", "--dataset", "empty_ds"])

    assert result.exit_code == 0
    assert "no tables" in result.output


def test_inspect_falls_back_to_state_for_dataset_and_project(invoke, fake_bigquery, state_file):
    """A bare ``data inspect`` inspects whatever the pipeline last built."""
    state_file(bq_dataset_id="dataset_from_state", gcp_project_id="project-from-state")
    fake_bigquery.datasets = {"dataset_from_state": []}

    result = invoke(["data", "inspect", "--json"])

    assert result.exit_code == 0
    payload = envelope(result)
    assert payload["data"]["dataset_id"] == "dataset_from_state"
    assert payload["data"]["project_id"] == (default_gcp_project() or "project-from-state")


def test_inspect_without_dataset_or_state_is_a_config_error(invoke, fake_bigquery):
    """A missing ``--dataset`` is diagnosed as a missing flag, not an absent dataset.

    This used to fall through to the ``FlowState`` default and surface as a
    ``RemoteApiError`` -- "Dataset `proj.logistics_analytics` does not exist" --
    which described the symptom of inspecting a dataset nobody had named rather
    than the cause. That sent the caller to look for a warehouse problem that
    did not exist. Exit 4 naming ``--dataset`` is the actionable form, and the
    ``option`` detail is what lets an agent retry with the right flag.
    """
    result = invoke(["data", "inspect", "--json"])

    assert result.exit_code == ConfigError.exit_code
    payload = envelope(result)
    assert payload["status"] == "FAILED"
    assert payload["errors"][0]["code"] == "CONFIG_ERROR"
    assert payload["errors"][0]["details"] == {"option": "--dataset"}
    assert "demo-create data generate" in payload["errors"][0]["remediation"]


def test_inspect_explicit_flag_overrides_state(invoke, fake_bigquery, state_file):
    """An explicit flag must always win over persisted state."""
    state_file(bq_dataset_id="dataset_from_state")
    fake_bigquery.datasets = {"explicit_ds": []}

    result = invoke(["data", "inspect", "--dataset", "explicit_ds", "--json"])

    assert result.exit_code == 0
    assert envelope(result)["data"]["dataset_id"] == "explicit_ds"


def test_inspect_help_lists_current_option_names(invoke):
    """Guards against an option being renamed without the docs following."""
    result = invoke(["data", "inspect", "--help"])

    assert result.exit_code == 0
    for flag in ("--dataset", "--gcp-project", "--location", "--json"):
        assert flag in result.stdout


# ===========================================================================
# data group
# ===========================================================================


def test_data_group_help_lists_three_subcommands(invoke):
    """The group's surface area is exactly generate/upload/inspect."""
    result = invoke(["data", "--help"])

    assert result.exit_code == 0
    for sub in ("generate", "upload", "inspect"):
        assert sub in result.stdout


def test_data_group_rejects_unknown_subcommand(invoke):
    """Exit 2 is Click's usage code, reserved and never reused by errors.py."""
    result = invoke(["data", "nonexistent"])

    assert result.exit_code == 2
