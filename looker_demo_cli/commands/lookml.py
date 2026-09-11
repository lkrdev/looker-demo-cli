"""``demo-create lookml`` -- LookML generation, optimization, and deployment."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.config import DEFAULT_GCP_PROJECT
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import ConfigError, StateError, ValidationError, missing_option
from looker_demo_cli.generators.lookml_generator import LookMLGenerator, LookMLTableSpec
from looker_demo_cli.output import CommandResult, ErrorDetail, emit
from looker_demo_cli.services.deploy_service import deploy_lookml_project
from looker_demo_cli.services.knowledge_service import introspect_bq_table_specs
from looker_demo_cli.services.lookml_cleaner import clean_root_duplicate_files
from looker_demo_cli.services.optimizer_service import (
    optimize_lookml_project,
    render_optimization_report,
    restore_lookml_backup,
)
from looker_demo_cli.services.schema_service import extract_table_specs_from_parquet_dir
from looker_demo_cli.utils.console import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

lookml_app = typer.Typer(
    name="lookml",
    help="Generate LookML models from BigQuery/Knowledge Catalog or Parquet, and deploy.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


@lookml_app.command(name="model")
def lookml_model(
    ctx: typer.Context,
    dataset: Annotated[str | None, typer.Option("--dataset", help="Existing BigQuery dataset ID to model")] = None,
    tables: Annotated[
        str | None, typer.Option("--tables", help="Comma-separated list of table names to include")
    ] = None,
    parquet_dir: Annotated[
        Path | None, typer.Option("--parquet-dir", help="Directory containing Parquet files")
    ] = None,
    looker_project: Annotated[
        str | None, typer.Option("--looker-project", help="Looker project and model name")
    ] = None,
    connection: Annotated[
        str | None,
        typer.Option("--connection", help="Looker database connection name; reused from prior state if omitted"),
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="Directory to output generated LookML files")
    ] = None,
    gcp_project: Annotated[str, typer.Option("--gcp-project", help="Target GCP Project ID")] = DEFAULT_GCP_PROJECT,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Generate LookML views, explores, and dashboards from BigQuery or Parquet.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        dataset: BigQuery dataset to introspect. Falls back to prior state.
        tables: Comma-separated allow-list of table names.
        parquet_dir: Local Parquet directory, used when no dataset is available.
        looker_project: Looker project and model name. Spelled ``--looker-project``
            rather than ``--project`` so it cannot be confused with
            ``--gcp-project``.
        connection: Looker database connection the generated model binds to.
            Falls back to the connection recorded by a previous run. There is
            no default: a wrong connection name yields a model that parses and
            deploys but cannot run a single query.
        output_dir: Where to write the generated LookML tree.
        gcp_project: Target Google Cloud project.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: No Looker project name, no database connection, or no
            tables could be resolved.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    proj_name = looker_project or state.looker_project_name
    if not proj_name:
        # This gate is what *names* the project; nothing upstream can supply it,
        # and the name is written into every generated file plus the Looker
        # remote it later deploys to, so guessing one is not recoverable.
        raise missing_option(
            "--looker-project",
            purpose="the Looker project and model name to generate",
            hint="This gate names the project; pick a name for the demo, e.g. --looker-project retail_analytics.",
        )
    # The dataset legitimately derives from the project name: for a demo built
    # end to end the two match, and the `dataset is None` warning below tells
    # the caller exactly which warehouse table the views were pointed at.
    ds_name = dataset or state.bq_dataset_id or proj_name
    conn_name = connection or state.looker_connection_name
    if not conn_name:
        raise missing_option(
            "--connection",
            purpose="the Looker database connection the generated model binds to",
            hint="Run `demo-create pre-check --json` to list the connections defined on the target instance.",
        )
    gcp_proj = gcp_project or state.gcp_project_id
    out_dir = output_dir or (Path.home() / "scratch" / "demo_create" / f"lookml_{proj_name}")
    out_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if dataset is None:
        # The generated `sql_table_name` is baked from ds_name, so a silent
        # fallback here produces views that point at the wrong warehouse table
        # rather than merely mis-recording state.
        warnings.append(
            f"No --dataset given; modelling `{gcp_proj}.{ds_name}` resolved from prior state. "
            "Pass --dataset explicitly to be certain."
        )

    table_filter = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    table_specs: list[LookMLTableSpec] = []
    source = "none"

    # 1. Source: Live BigQuery Dataset with Knowledge Catalog semantics
    if dataset or (state.dataset_exists and not parquet_dir):
        print_info(f"Introspecting BigQuery dataset `{gcp_proj}.{ds_name}` and querying Knowledge Catalog semantics...")
        table_specs = introspect_bq_table_specs(
            project_id=gcp_proj,
            dataset_id=ds_name,
            location=state.gcp_location,
            table_filter=table_filter,
        )
        if table_specs:
            source = "bigquery"
            print_success(f"Discovered and enriched {len(table_specs)} table(s) via BigQuery & Knowledge Catalog.")

    # 2. Source: Local Parquet files
    if not table_specs:
        p_dir = parquet_dir or state.generated_parquet_dir
        if p_dir and p_dir.exists() and any(p_dir.glob("*.parquet")):
            print_info(f"Extracting table specifications from Parquet directory: `{p_dir}`...")
            all_specs = extract_table_specs_from_parquet_dir(p_dir)
            table_specs = [s for s in all_specs if not table_filter or s.table_name in table_filter]
            source = "parquet"
            print_success(f"Extracted {len(table_specs)} table spec(s) from Parquet files.")

    if not table_specs:
        raise ConfigError(
            "No tables found to model.",
            remediation=(
                "Pass --dataset <bigquery-dataset> or --parquet-dir <dir>, or run `demo-create data generate` first."
            ),
            details={"dataset": ds_name, "gcp_project": gcp_proj},
        )

    print_info(f"Generating LookML files into `{out_dir}`...")
    gen = LookMLGenerator(project_id=gcp_proj, dataset_id=ds_name, connection_name=conn_name)
    written = gen.write_lookml_project_files(
        output_dir=out_dir,
        model_name=proj_name,
        tables=table_specs,
    )

    state.looker_project_name = proj_name
    state.lookml_model_name = proj_name
    state.bq_dataset_id = ds_name
    state.looker_connection_name = conn_name
    state.lookml_output_dir = out_dir
    state.gcp_project_id = gcp_proj
    state.existing_tables = [s.table_name for s in table_specs]
    saved_path = app_ctx.save_state()

    result = CommandResult.success(
        "lookml model",
        data={
            "looker_project": proj_name,
            "model": proj_name,
            "dataset": ds_name,
            "gcp_project": gcp_proj,
            "connection": conn_name,
            "source": source,
            "output_dir": str(out_dir),
            "tables": [s.table_name for s in table_specs],
            "files": [str(f.relative_to(out_dir)) for f in written],
            "state_file": str(saved_path),
        },
        warnings=warnings,
    ).add_next_action(
        "Validate and deploy the generated LookML to Looker",
        f"demo-create lookml deploy --looker-project {proj_name} --lookml-dir {out_dir}",
        gate=3,
        requires_human_confirmation=True,
    )

    def render(res: CommandResult) -> None:
        for warning in res.warnings:
            print_warning(warning)
        print_success(f"Generated {len(written)} LookML files in `{out_dir}`:")
        for f in written:
            console.print(f"  • {f.relative_to(out_dir)}")
        print_info(f"Updated state saved to `{saved_path}`")

    return emit(result, json_output=output_json, human_renderer=render)


@lookml_app.command(name="deploy")
def lookml_deploy(
    ctx: typer.Context,
    lookml_dir: Annotated[Path | None, typer.Option("--lookml-dir", help="Directory containing LookML files")] = None,
    looker_project: Annotated[str | None, typer.Option("--looker-project", help="Looker project name")] = None,
    account: Annotated[str | None, typer.Option("--looker-account", help="Saved Looker OAuth account alias")] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Push local LookML to the dev workspace, validate it, and deploy to production.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        lookml_dir: Directory of staged LookML files.
        looker_project: Looker project name to push into. Spelled
            ``--looker-project`` rather than ``--project`` so it cannot be
            confused with ``--gcp-project``.
        account: Saved ``lkr`` OAuth account alias.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: No LookML directory could be resolved.
        ValidationError: The push, the LookML validator, or a dashboard tile
            query failed. Previously this exited 0, so an orchestrator chaining
            on ``&&`` would continue as though production had been updated.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    if lookml_dir:
        state.lookml_output_dir = lookml_dir
    if looker_project:
        state.looker_project_name = looker_project
        state.lookml_model_name = looker_project
    if account:
        state.looker_account = account

    if not state.lookml_output_dir or not state.lookml_output_dir.exists():
        raise ConfigError(
            "No LookML directory found to deploy.",
            remediation="Pass --lookml-dir <dir>, or run `demo-create lookml model` first.",
            details={"lookml_dir": str(state.lookml_output_dir) if state.lookml_output_dir else None},
        )

    final_state = deploy_lookml_project(state)
    # The step returns the state it worked on; adopt it rather than assuming it
    # mutated `state` in place, so a future copy-on-write step cannot silently
    # drop its own results at save time.
    app_ctx.set_state(final_state)
    saved_path = app_ctx.save_state()

    if final_state.status == "failed":
        raise ValidationError(
            final_state.error_message or "LookML deployment failed.",
            remediation=(
                "Review the validator and query-test output above, fix the reported LookML, "
                "and re-run `demo-create lookml deploy`."
            ),
            details={
                "looker_project": final_state.looker_project_name,
                "lookml_dir": str(final_state.lookml_output_dir),
                "state_file": str(saved_path),
            },
        )

    result = CommandResult.success(
        "lookml deploy",
        data={
            "looker_project": final_state.looker_project_name,
            "model": final_state.lookml_model_name,
            "lookml_dir": str(final_state.lookml_output_dir),
            "instance_url": final_state.looker_instance_url,
            "dashboard_url": final_state.deployed_dashboard_url,
            "state_file": str(saved_path),
        },
    ).add_next_action(
        "Provision the Conversational Analytics agent for the deployed model",
        f"demo-create agent create --model {final_state.lookml_model_name}",
        gate=4,
        requires_human_confirmation=True,
    )

    def render(_: CommandResult) -> None:
        print_info(f"Updated state saved to `{saved_path}`")

    return emit(result, json_output=output_json, human_renderer=render)


@lookml_app.command(name="optimize")
def lookml_optimize(
    ctx: typer.Context,
    lookml_dir: Annotated[
        Path | None,
        typer.Option("--lookml-dir", help="Directory containing LookML files"),
    ] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    backup: Annotated[
        bool, typer.Option("--backup/--no-backup", help="Snapshot LookML files into .backup_pre_opt before patching")
    ] = True,
    state_file: StateFileOption = None,
):
    """Patch staged LookML in place with Google Cloud server performance best practices.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        lookml_dir: Directory of staged LookML files.
        output_json: Emit the JSON envelope on stdout.
        backup: Snapshot the directory into ``.backup_pre_opt`` before patching,
            so ``demo-create lookml restore`` can roll the changes back.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: The target directory does not exist.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    target_dir = lookml_dir or state.lookml_output_dir or Path("lookml")
    if not target_dir.exists():
        raise ConfigError(
            f"LookML directory `{target_dir}` does not exist.",
            remediation="Pass --lookml-dir <dir>, or run `demo-create lookml model` first.",
            details={"lookml_dir": str(target_dir)},
        )

    print_info(f"Auditing and optimizing LookML files in `{target_dir}`...")
    report = optimize_lookml_project(target_dir, backup=backup)

    result = CommandResult.success(
        "lookml optimize",
        data={"lookml_dir": str(target_dir), "backup": backup, **report},
    )
    if backup:
        result.add_next_action(
            "Roll back the optimization if the patched LookML is unsatisfactory",
            f"demo-create lookml restore --lookml-dir {target_dir}",
        )

    return emit(
        result,
        json_output=output_json,
        human_renderer=lambda _: render_optimization_report(report),
    )


@lookml_app.command(name="restore")
def lookml_restore(
    ctx: typer.Context,
    lookml_dir: Annotated[
        Path | None,
        typer.Option("--lookml-dir", help="Directory containing LookML files"),
    ] = None,
    output_json: Annotated[bool, typer.Option("--json", help="Emit the result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Restore LookML files from the ``.backup_pre_opt`` snapshot.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        lookml_dir: Directory holding the ``.backup_pre_opt`` snapshot.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: The target directory does not exist.
        StateError: No usable snapshot was found to restore from.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    target_dir = lookml_dir or state.lookml_output_dir or Path("lookml")
    if not target_dir.exists():
        raise ConfigError(
            f"LookML directory `{target_dir}` does not exist.",
            remediation="Pass --lookml-dir <dir> pointing at the optimized project.",
            details={"lookml_dir": str(target_dir)},
        )

    print_info(f"Restoring LookML files from snapshot in `{target_dir}`...")
    report = restore_lookml_backup(target_dir)

    if report.get("status") != "SUCCESS":
        raise StateError(
            report.get("error", "Failed to restore backup."),
            remediation="Confirm `.backup_pre_opt` exists; it is only created by `demo-create lookml optimize`.",
            details={"lookml_dir": str(target_dir), **report},
        )

    restored = report.get("files_restored", [])
    result = CommandResult.success(
        "lookml restore",
        data={"lookml_dir": str(target_dir), "files_restored": restored, **report},
    )

    def render(_: CommandResult) -> None:
        print_success(f"Successfully restored {len(restored)} LookML file(s) from `.backup_pre_opt`:")
        for f in restored:
            console.print(f"  • {f}")

    return emit(result, json_output=output_json, human_renderer=render)


@lookml_app.command(name="clean-root")
def lookml_clean_root(
    ctx: typer.Context,
    looker_project: Annotated[
        str | None,
        typer.Option("--looker-project", help="Looker project name to audit and clean"),
    ] = None,
    account: Annotated[
        str | None,
        typer.Option("--looker-account", help="Saved Looker OAuth account alias"),
    ] = None,
    instance_url: Annotated[
        str | None,
        typer.Option("--instance", help="Looker instance base URL"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report root duplicates without deleting them"),
    ] = False,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit the result envelope as JSON on stdout"),
    ] = False,
    state_file: StateFileOption = None,
):
    """Delete duplicate root-level LookML files that shadow their subfolder copies.

    For example ``users.view.lkml`` at the project root alongside
    ``views/users.view.lkml``. These orphans desync the remote master branch.

    Args:
        ctx: Typer context carrying the resolved :class:`AppContext`.
        looker_project: Looker project to audit. Spelled ``--looker-project``
            rather than ``--project`` so it cannot be confused with
            ``--gcp-project``.
        account: Saved ``lkr`` OAuth account alias.
        instance_url: Looker instance base URL.
        dry_run: Report the duplicates without deleting them.
        output_json: Emit the JSON envelope on stdout.
        state_file: Optional explicit path to ``.demo-state.json``.

    Returns:
        The emitted result envelope.

    Raises:
        ConfigError: No Looker project could be resolved.
        AuthError: No usable Looker credentials.
        RemoteApiError: The project files could not be listed.
    """
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)

    state = app_ctx.state
    proj_name = looker_project or state.looker_project_name
    if not proj_name:
        raise missing_option(
            "--looker-project",
            purpose="the Looker project to audit",
            hint="Or run `demo-create lookml model` first to record one.",
        )

    auth = app_ctx.looker_auth(instance_url, account)

    print_info(f"Auditing Looker project `{proj_name}` for orphaned root duplicate files...")
    report = clean_root_duplicate_files(
        project_id=proj_name,
        headers=auth.headers,
        base_url=auth.base_url,
        dry_run=dry_run,
    )

    cleaned = report.get("cleaned_files", [])
    report_status = report.get("status", "SUCCESS")
    data = {"looker_project": proj_name, "dry_run": dry_run, **report}

    if report_status == "PARTIAL":
        # PARTIAL exits non-zero: some duplicates survived, so the remote master
        # branch is still desynced and a deploy would resurrect them.
        result = CommandResult(
            command="lookml clean-root",
            status="PARTIAL",
            data=data,
            errors=[
                ErrorDetail(
                    code="REMOTE_API_ERROR",
                    message=(
                        f"Deleted {report.get('total_deleted', 0)} of "
                        f"{report.get('total_detected', 0)} duplicate root file(s)."
                    ),
                    remediation="Re-run with credentials holding `develop` on the project.",
                    details={"cleaned_files": cleaned},
                )
            ],
        )
    else:
        result = CommandResult(
            command="lookml clean-root",
            status="DRY_RUN" if report_status == "DRY_RUN" else "SUCCESS",
            data=data,
        )

    def render(res: CommandResult) -> None:
        if res.status == "DRY_RUN":
            print_warning(f"Dry run: {len(cleaned)} duplicate root file(s) identified.")
        elif cleaned:
            print_success(f"Cleaned {len(cleaned)} duplicate root file(s) from `{proj_name}`.")
        elif res.ok:
            print_success(f"Project `{proj_name}` has a clean root directory structure. No duplicates found.")
        for f in cleaned:
            console.print(f"  • {f}")
        for error in res.errors:
            print_error(error.message)

    return emit(result, json_output=output_json, human_renderer=render)
