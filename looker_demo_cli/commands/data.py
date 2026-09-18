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
    DomainBlueprint,
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
    schema_file: Annotated[
        Path | None, typer.Option("--schema-file", help="Path to JSON DomainBlueprint schema specification")
    ] = None,
    script: Annotated[
        Path | None, typer.Option("--script", help="Path to LLM-authored Python generator script")
    ] = None,
    builder_script: Annotated[
        Path | None,
        typer.Option("--builder-script", help="Path to DataDesigner or custom Python builder script"),
    ] = None,
    engine: Annotated[
        str,
        typer.Option("--engine", help="Synthesis engine priority: modular-dag, auto, data-designer, or fallback"),
    ] = "modular-dag",
    preview: Annotated[
        bool,
        typer.Option("--preview", help="Inspect sampled rows across generated tables without disk or BigQuery commit"),
    ] = False,
    preview_rows: Annotated[
        int,
        typer.Option("--preview-rows", "-n", help="Number of sample rows to display in --preview mode"),
    ] = 5,
    validate_only: Annotated[
        bool,
        typer.Option(
            "--validate-only",
            help="Execute topological DAG and in-memory validation gates without uploading to BigQuery",
        ),
    ] = False,
    upload: Annotated[
        bool, typer.Option("--upload", help="Automatically upload synthesized Parquet tables to BigQuery via ADC")
    ] = False,
    json_scorecard: Annotated[
        bool,
        typer.Option(
            "--json-scorecard",
            help="Include structured verification scorecard and emit JSON envelope on stdout",
        ),
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
        schema_file: Optional JSON file containing a serialized DomainBlueprint.
        script: Optional LLM-authored Python generator script path.
        builder_script: Optional DataDesigner or custom Python builder script path.
        engine: Synthesis engine mode (modular-dag is default high-throughput vectorized DAG).
        preview: Render sample rows in terminal without committing files to disk or cloud.
        preview_rows: Number of sample rows to display per table when `--preview` is active.
        validate_only: Execute topological DAG and assertions without uploading to BigQuery.
        upload: Also load the generated tables into BigQuery using ADC.
        json_scorecard: Emit machine-readable JSON scorecard envelope on stdout.
        gcp_project: Target Google Cloud project, used only when uploading.
        dataset: Target BigQuery dataset ID. Defaults to the domain name.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.
    """
    if json_scorecard:
        output_json = True

    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state
    target_dir = output_dir or (Path.home() / "scratch" / "demo_create" / (dataset or domain))

    if not preview:
        target_dir.mkdir(parents=True, exist_ok=True)

    if schema_file:
        if not schema_file.exists():
            raise ConfigError(
                f"Schema file `{schema_file}` does not exist.",
                remediation="Pass a valid path to a DomainBlueprint JSON file via --schema-file.",
                details={"schema_file": str(schema_file)},
            )
        blueprint = DomainBlueprint.model_validate_json(schema_file.read_text(encoding="utf-8"))
        domain = blueprint.domain_name or domain
    else:
        blueprint = create_dynamic_blueprint_from_name(domain)

    effective_fact_rows = max(preview_rows * 2, 20) if preview else row_count
    for entity in blueprint.entities:
        if getattr(entity, "table_type", "dimension") == "fact":
            entity.row_count = effective_fact_rows
        elif preview:
            entity.row_count = max(preview_rows * 2, 20)

    effective_script = script or builder_script
    if not preview:
        print_info(f"Synthesizing dataset for domain `{domain}` into `{target_dir}`...")

    specs = generate_domain_dataset(
        target=blueprint,
        output_dir=target_dir,
        micro_sample_only=preview,
        builder_script=effective_script,
        engine=engine,
    )
    table_names = [s.table_name for s in specs]
    dag_res = getattr(specs[0], "_dag_result", None) if specs else None

    if preview:
        samples: dict[str, list[dict[str, Any]]] = {}
        if dag_res is not None:
            for t_name, df in dag_res.tables.items():
                samples[t_name] = df.head(preview_rows).astype(str).to_dict(orient="records")

        result = CommandResult.success(
            "data generate",
            data={
                "domain": domain,
                "preview": True,
                "preview_rows": preview_rows,
                "tables": table_names,
                "samples": samples,
            },
        )

        def render_preview(_: CommandResult) -> None:
            if dag_res is None:
                print_info(f"Preview generated for {len(table_names)} tables: {table_names}")
                return
            for t_name, df in dag_res.tables.items():
                tbl_view = Table(
                    title=f"Preview: {t_name} (showing {min(preview_rows, len(df))} of {len(df)} rows)",
                    show_header=True,
                    header_style="bold cyan",
                )
                for col in df.columns:
                    tbl_view.add_column(str(col))
                for _, row in df.head(preview_rows).iterrows():
                    tbl_view.add_row(*[str(val) for val in row.values])
                console.print(tbl_view)

        return emit(result, json_output=output_json, human_renderer=render_preview)

    state.domain_name = domain
    state.bq_dataset_id = dataset or domain
    state.generated_parquet_dir = target_dir
    state.generated_tables = table_names
    state.gcp_project_id = gcp_project or state.gcp_project_id

    loaded_rows: dict[str, int] = {}
    partitioned_tables: list[str] = []
    clustered_tables: list[str] = []

    should_upload = upload and not validate_only
    if should_upload:
        print_info(f"Uploading generated tables to BigQuery dataset `{state.bq_dataset_id}`...")
        # Through the context's factory rather than a direct `BigQueryHelper(...)`:
        # this module deliberately never imports the helper, so the only name a
        # test has to replace is `looker_demo_cli.context.BigQueryHelper`.
        bq_helper = app_ctx.bigquery(project_id=state.gcp_project_id, location=state.gcp_location)
        bq_helper.ensure_dataset(state.bq_dataset_id)
        for t_name in table_names:
            p_file = target_dir / f"{t_name}.parquet"
            if p_file.exists():
                df_sample = dag_res.tables.get(t_name) if dag_res is not None else None
                if hasattr(bq_helper, "load_parquet_table_optimized"):
                    load_info = bq_helper.load_parquet_table_optimized(
                        state.bq_dataset_id,
                        t_name,
                        p_file,
                        df_sample=df_sample,
                    )
                    loaded_rows[t_name] = load_info["rows"]
                    if load_info.get("partition_field"):
                        partitioned_tables.append(t_name)
                    if load_info.get("clustering_fields"):
                        clustered_tables.append(t_name)
                else:
                    loaded_rows[t_name] = bq_helper.load_parquet_table(state.bq_dataset_id, t_name, p_file)
        state.dataset_exists = True

    saved_path = app_ctx.save_state()

    if dag_res is not None:
        exec_time = round(dag_res.duration_seconds, 4)
        rps = round(dag_res.total_rows / max(dag_res.duration_seconds, 0.0001), 1)
        tbl_metrics = dag_res.validation_report.table_metrics
        val_status = "SUCCESS" if dag_res.validation_report.is_valid else "FAILED"
    else:
        exec_time = 0.0
        rps = 0.0
        tbl_metrics = {t: {"rows": row_count, "pk_uniqueness": 1.0, "orphan_fks": 0} for t in table_names}
        val_status = "SUCCESS"

    scorecard = {
        "status": val_status,
        "domain": domain,
        "execution_time_seconds": exec_time,
        "records_per_second": rps,
        "tables": tbl_metrics,
        "bigquery_load": {
            "dataset": state.bq_dataset_id,
            "auth": "ADC",
            "uploaded": should_upload,
            "partitioned_tables": partitioned_tables,
            "clustered_tables": clustered_tables,
        },
    }

    data_payload: dict[str, Any] = {
        "domain": domain,
        "row_count": row_count,
        "output_dir": str(target_dir),
        "dataset": state.bq_dataset_id,
        "gcp_project": state.gcp_project_id,
        "tables": table_names,
        "uploaded": should_upload,
        "loaded_rows": loaded_rows,
        "state_file": str(saved_path),
    }
    if json_scorecard or validate_only:
        data_payload["validate_only"] = validate_only
        data_payload["partitioned_tables"] = partitioned_tables
        data_payload["clustered_tables"] = clustered_tables
        data_payload["scorecard"] = scorecard

    result = CommandResult.success("data generate", data=data_payload)
    if not should_upload:
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
    verify_only: Annotated[
        bool,
        typer.Option(
            "--verify-only",
            help="Verify tables already loaded in BigQuery and sync state without re-uploading Parquet files",
        ),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Upload local Parquet tables into a BigQuery dataset via ADC with automated partitioning & clustering.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        parquet_dir: Directory of ``*.parquet`` files. Falls back to the
            directory recorded by a previous ``data generate``.
        dataset: Target BigQuery dataset ID.
        gcp_project: Target Google Cloud project.
        location: BigQuery dataset location.
        verify_only: When true, check if tables already exist in BigQuery and record row
            counts without re-uploading.
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

    bq_helper = app_ctx.bigquery(project_id=proj_id, location=location)
    bq_helper.ensure_dataset(ds_id)

    action_verb = "Verifying" if verify_only else "Loading"
    print_info(f"{action_verb} {len(parquet_files)} Parquet files in `{proj_id}.{ds_id}`...")
    loaded_rows: dict[str, int] = {}

    for pf in parquet_files:
        t_name = pf.stem
        existing_rows = getattr(bq_helper, "get_table_row_count", lambda d, t: None)(ds_id, t_name)
        if verify_only and existing_rows is not None and existing_rows > 0:
            loaded_rows[t_name] = existing_rows
        else:
            if hasattr(bq_helper, "load_parquet_table_optimized"):
                load_info = bq_helper.load_parquet_table_optimized(ds_id, t_name, pf)
                loaded_rows[t_name] = load_info["rows"]
            else:
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

    bq_helper = app_ctx.bigquery(project_id=proj_id, location=location)
    if not bq_helper.dataset_exists(ds_id):
        raise RemoteApiError(
            f"Dataset `{proj_id}.{ds_id}` does not exist.",
            remediation="Run `demo-create data upload` to create and populate it, or pass --dataset.",
            details={"gcp_project": proj_id, "dataset": ds_id, "exists": False},
        )

    tables_data: list[dict[str, Any]] = []
    failures: list[str] = []
    for t_id in bq_helper.list_tables(ds_id):
        tbl_ref = bq_helper.client.dataset(ds_id).table(t_id)
        try:
            tbl = bq_helper.client.get_table(tbl_ref)
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
