"""``demo-create data`` -- synthetic dataset generation and BigQuery loading."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.config import DEFAULT_GCP_PROJECT
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import ConfigError, RemoteApiError, missing_option
from looker_demo_cli.generators.schema_generator import (
    create_dynamic_blueprint_from_name,
    generate_domain_dataset,
)
from looker_demo_cli.output import CommandResult, ErrorDetail, emit
from looker_demo_cli.utils.console import console, print_error, print_info, print_success, print_warning

data_app = typer.Typer(
    name="data",
    help="Design, synthesize, inspect, and upload BigQuery demo datasets.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


@data_app.command(name="generate")
def data_generate(
    ctx: typer.Context,
    domain: Annotated[
        str, typer.Option("--domain", help="Domain theme name (e.g. supply_chain, trucking_iot)")
    ] = "logistics_analytics",
    row_count: Annotated[int, typer.Option("--row-count", help="Target fact row count")] = 1000,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="Local directory to write Parquet files")
    ] = None,
    upload: Annotated[
        bool, typer.Option("--upload", help="Automatically upload synthesized Parquet tables to BigQuery")
    ] = False,
    gcp_project: Annotated[
        str, typer.Option("--gcp-project", help="Target GCP Project ID if uploading")
    ] = DEFAULT_GCP_PROJECT,
    dataset: Annotated[str | None, typer.Option("--dataset", help="Target BigQuery dataset ID if uploading")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Synthesize high-fidelity relational Parquet dataset tables locally.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        domain: Domain theme driving the generated schema and value distributions.
        row_count: Row count applied to every fact table in the blueprint.
        output_dir: Where to write the Parquet files. Defaults to a scratch
            directory under the user's home.
        upload: Also load the generated tables into BigQuery.
        gcp_project: Target Google Cloud project, used only when uploading.
        dataset: Target BigQuery dataset ID. Defaults to the domain name.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state
    target_dir = output_dir or (Path.home() / "scratch" / "demo_create" / (dataset or domain))
    target_dir.mkdir(parents=True, exist_ok=True)

    print_info(f"Synthesizing dataset for domain `{domain}` into `{target_dir}`...")
    blueprint = create_dynamic_blueprint_from_name(domain)
    for entity in blueprint.entities:
        if entity.table_type == "fact":
            entity.row_count = row_count

    specs = generate_domain_dataset(target=blueprint, output_dir=target_dir)
    table_names = [s.table_name for s in specs]

    state.domain_name = domain
    state.bq_dataset_id = dataset or domain
    state.generated_parquet_dir = target_dir
    state.generated_tables = table_names
    state.gcp_project_id = gcp_project or state.gcp_project_id

    loaded_rows: dict[str, int] = {}
    if upload:
        print_info(f"Uploading generated tables to BigQuery dataset `{state.bq_dataset_id}`...")
        # Through the context's factory rather than a direct `BigQueryHelper(...)`:
        # this module deliberately never imports the helper, so the only name a
        # test has to replace is `looker_demo_cli.context.BigQueryHelper`.
        bq_helper = app_ctx.bigquery(project_id=state.gcp_project_id, location=state.gcp_location)
        bq_helper.ensure_dataset(state.bq_dataset_id)
        for t_name in table_names:
            p_file = target_dir / f"{t_name}.parquet"
            if p_file.exists():
                loaded_rows[t_name] = bq_helper.load_parquet_table(state.bq_dataset_id, t_name, p_file)
        state.dataset_exists = True

    saved_path = app_ctx.save_state()

    result = CommandResult.success(
        "data generate",
        data={
            "domain": domain,
            "row_count": row_count,
            "output_dir": str(target_dir),
            "dataset": state.bq_dataset_id,
            "gcp_project": state.gcp_project_id,
            "tables": table_names,
            "uploaded": upload,
            "loaded_rows": loaded_rows,
            "state_file": str(saved_path),
        },
    )
    if not upload:
        result.add_next_action(
            "Load the generated Parquet tables into BigQuery",
            f"demo-create data upload --parquet-dir {target_dir} --dataset {state.bq_dataset_id}",
            gate=1,
            requires_human_confirmation=True,
        )

    def render(_: CommandResult) -> None:
        print_success(f"Generated {len(table_names)} tables in `{target_dir}`: {table_names}")
        for name, rows in loaded_rows.items():
            print_success(f"Loaded `{name}` ({rows:,} rows) into BigQuery")
        print_info(f"Updated state saved to `{saved_path}`")

    return emit(result, json_output=output_json, human_renderer=render)


@data_app.command(name="upload")
def data_upload(
    ctx: typer.Context,
    parquet_dir: Annotated[
        Path | None, typer.Option("--parquet-dir", help="Directory containing Parquet files")
    ] = None,
    dataset: Annotated[str | None, typer.Option("--dataset", help="Target BigQuery dataset ID")] = None,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    location: Annotated[str, typer.Option("--location", help="BigQuery dataset location")] = "US",
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Upload local Parquet tables into a BigQuery dataset.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        parquet_dir: Directory of ``*.parquet`` files. Falls back to the
            directory recorded by a previous ``data generate``.
        dataset: Target BigQuery dataset ID.
        gcp_project: Target Google Cloud project.
        location: BigQuery dataset location.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        ConfigError: No Parquet directory could be resolved, it holds no
            ``*.parquet`` files, or no target dataset could be resolved.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    # Every input is validated before the BigQuery client is constructed.
    # `ensure_dataset` creates the dataset, so validating afterwards -- as this
    # command used to -- leaves an empty orphan dataset behind whenever the
    # Parquet directory turns out to be missing or empty.
    p_dir = parquet_dir or state.generated_parquet_dir
    if not p_dir or not p_dir.exists():
        raise ConfigError(
            "No Parquet directory found.",
            remediation="Pass --parquet-dir <dir>, or run `demo-create data generate` first.",
            details={"parquet_dir": str(p_dir) if p_dir else None},
        )

    parquet_files = sorted(p_dir.glob("*.parquet"))
    if not parquet_files:
        raise ConfigError(
            f"No .parquet files found in `{p_dir}`.",
            remediation="Run `demo-create data generate --output-dir <dir>` to synthesize tables first.",
            details={"parquet_dir": str(p_dir)},
        )

    ds_id = dataset or state.bq_dataset_id
    if not ds_id:
        raise missing_option(
            "--dataset",
            purpose="the BigQuery dataset to load the Parquet tables into",
            hint="Or run `demo-create data generate` first, which records the dataset it synthesized for.",
        )
    proj_id = gcp_project or state.gcp_project_id

    # See the note in `data_generate`: the helper is resolved through the
    # context so that no import site here has to be patched in tests.
    bq_helper = app_ctx.bigquery(project_id=proj_id, location=location)
    bq_helper.ensure_dataset(ds_id)

    print_info(f"Loading {len(parquet_files)} Parquet files into `{proj_id}.{ds_id}`...")
    loaded_rows: dict[str, int] = {}
    for pf in parquet_files:
        t_name = pf.stem
        loaded_rows[t_name] = bq_helper.load_parquet_table(ds_id, t_name, pf)
        if t_name not in state.generated_tables:
            state.generated_tables.append(t_name)

    state.dataset_exists = True
    state.bq_dataset_id = ds_id
    state.gcp_project_id = proj_id
    state.gcp_location = location
    saved_path = app_ctx.save_state()

    result = CommandResult.success(
        "data upload",
        data={
            "gcp_project": proj_id,
            "dataset": ds_id,
            "location": location,
            "parquet_dir": str(p_dir),
            "loaded_rows": loaded_rows,
            "total_rows": sum(loaded_rows.values()),
            "state_file": str(saved_path),
        },
    ).add_next_action(
        "Generate the LookML model, explores, and dashboards",
        f"demo-create lookml model --dataset {ds_id} --gcp-project {proj_id}",
        gate=2,
        requires_human_confirmation=True,
    )

    def render(_: CommandResult) -> None:
        for name, rows in loaded_rows.items():
            print_success(f"Loaded `{name}` ({rows:,} rows)")
        print_info(f"Updated state saved to `{saved_path}`")

    return emit(result, json_output=output_json, human_renderer=render)


@data_app.command(name="inspect")
def data_inspect(
    ctx: typer.Context,
    dataset: Annotated[str | None, typer.Option("--dataset", help="BigQuery dataset ID")] = None,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    location: Annotated[str, typer.Option("--location", help="BigQuery dataset location")] = "US",
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Inspect tables, schemas, and metadata in a BigQuery dataset.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        dataset: BigQuery dataset ID. Falls back to the dataset in prior state.
        gcp_project: Target Google Cloud project.
        location: BigQuery dataset location.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Raises:
        ConfigError: No BigQuery dataset could be resolved.
        RemoteApiError: The dataset does not exist.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state
    ds_id = dataset or state.bq_dataset_id
    if not ds_id:
        raise missing_option(
            "--dataset",
            purpose="the BigQuery dataset to inspect",
            hint="Or run `demo-create data generate` or `demo-create data upload` first to record one.",
        )
    proj_id = gcp_project or state.gcp_project_id

    # See the note in `data_generate`: the helper is resolved through the
    # context so that no import site here has to be patched in tests.
    bq_helper = app_ctx.bigquery(project_id=proj_id, location=location)
    if not bq_helper.dataset_exists(ds_id):
        raise RemoteApiError(
            f"Dataset `{proj_id}.{ds_id}` does not exist.",
            remediation="Run `demo-create data upload` to create and populate it, or pass --dataset.",
            details={"gcp_project": proj_id, "dataset": ds_id, "exists": False},
        )

    # One collection pass feeds both renderings, so the JSON and the table can
    # never disagree -- they previously walked the tables independently.
    tables_data: list[dict[str, Any]] = []
    failures: list[str] = []
    for t_id in bq_helper.list_tables(ds_id):
        tbl_ref = bq_helper.client.dataset(ds_id).table(t_id)
        try:
            tbl = bq_helper.client.get_table(tbl_ref)
        # Broad by design: any per-table read failure downgrades the whole
        # result to PARTIAL rather than aborting the inspection.
        except Exception as exc:
            tables_data.append({"table_id": t_id, "error": str(exc)})
            failures.append(t_id)
            continue
        tables_data.append(
            {
                "table_id": t_id,
                "table_type": tbl.table_type,
                "column_count": len(tbl.schema),
                "num_rows": tbl.num_rows,
                "columns": [{"name": f.name, "field_type": f.field_type, "mode": f.mode} for f in tbl.schema],
            }
        )

    data = {
        "project_id": proj_id,
        "dataset_id": ds_id,
        "location": location,
        "table_count": len(tables_data),
        "tables": tables_data,
    }

    if failures:
        # Previously these exceptions were swallowed into the payload while the
        # command still exited 0, so an orchestrator chaining on `&&` treated a
        # half-readable dataset as a healthy one.
        result = CommandResult(
            command="data inspect",
            status="PARTIAL",
            data=data,
            errors=[
                ErrorDetail(
                    code="REMOTE_API_ERROR",
                    message=f"Failed to read metadata for {len(failures)} table(s): {', '.join(failures)}.",
                    remediation="Confirm the caller holds bigquery.tables.get on every table in the dataset.",
                    details={"tables": failures},
                )
            ],
        )
    else:
        result = CommandResult.success("data inspect", data=data)
        if not tables_data:
            result.warnings.append(f"Dataset `{ds_id}` exists but has no tables.")

    def render(res: CommandResult) -> None:
        for warning in res.warnings:
            print_warning(warning)
        if not tables_data:
            return
        table_report = Table(title=f"BigQuery Dataset: {proj_id}.{ds_id}", show_header=True, header_style="bold blue")
        table_report.add_column("Table Name", style="bold")
        table_report.add_column("Type", style="cyan")
        table_report.add_column("Columns", justify="right")
        table_report.add_column("Rows", justify="right")
        for entry in tables_data:
            if "error" in entry:
                table_report.add_row(entry["table_id"], "UNKNOWN", "?", "?")
            else:
                table_report.add_row(
                    entry["table_id"],
                    entry["table_type"],
                    str(entry["column_count"]),
                    f"{entry['num_rows']:,}",
                )
        console.print(table_report)
        for error in res.errors:
            print_error(error.message)

    return emit(result, json_output=output_json, human_renderer=render)
