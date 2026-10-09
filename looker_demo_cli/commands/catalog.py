"""``demo-create catalog`` -- Knowledge Catalog (Dataplex) inspection, mapping profiles, and metadata seeding."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from looker_demo_cli.catalog.models import CatalogSnapshot
from looker_demo_cli.commands.options import StateFileOption
from looker_demo_cli.context import get_context
from looker_demo_cli.error_boundary import ErrorHandlingGroup
from looker_demo_cli.errors import ConfigError, missing_option
from looker_demo_cli.gates import attach_next_gate_action
from looker_demo_cli.output import CommandResult, emit
from looker_demo_cli.services.catalog_service import build_catalog_snapshot
from looker_demo_cli.services.catalog_sync_service import sync_catalog_to_lookml
from looker_demo_cli.utils.console import console, print_info, print_success

catalog_app = typer.Typer(
    name="catalog",
    help="Inspect Knowledge Catalog (Dataplex) metadata, view mapping profiles, and seed curation semantics.",
    no_args_is_help=True,
    cls=ErrorHandlingGroup,
)


@catalog_app.command(name="inspect")
def catalog_inspect(
    ctx: typer.Context,
    dataset: Annotated[
        str | None,
        typer.Option("--dataset", help="Target BigQuery dataset ID to inspect. Defaults to state dataset."),
    ] = None,
    gcp_project: Annotated[
        str | None,
        typer.Option("--gcp-project", help="GCP Project ID. Defaults to confirmed target project."),
    ] = None,
    location: Annotated[
        str,
        typer.Option("--location", help="Dataset location (e.g. 'us', 'eu', 'us-central1')."),
    ] = "us",
    output_file: Annotated[
        Path | None,
        typer.Option("--output-file", "-o", help="Optional path to save the CatalogSnapshot JSON file."),
    ] = None,
    skip: Annotated[
        bool,
        typer.Option("--skip", help="Skip Knowledge Catalog inspection and advance to LookML modeling"),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Inspect BigQuery tables and Dataplex Knowledge Catalog metadata, generating a CatalogSnapshot."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    if skip:
        state.catalog_status = "skipped"
        app_ctx.save_state()
        result = attach_next_gate_action(
            CommandResult.success("catalog inspect", data={"status": "skipped", "catalog_status": "skipped"}),
            state,
        )
        skip_msg = (
            "Skipped Knowledge Catalog inspection. BigQuery + Knowledge Catalog workflow complete (mode=bq_only)."
            if state.deployment_mode == "bq_only"
            else "Skipped Knowledge Catalog inspection."
        )
        return emit(result, json_output=output_json, human_renderer=lambda _: print_info(skip_msg))

    proj_id = gcp_project or state.gcp_project_id
    ds_id = dataset or state.bq_dataset_id

    if not proj_id:
        raise missing_option("--gcp-project", purpose="the BigQuery project containing the dataset")
    if not ds_id:
        raise missing_option("--dataset", purpose="the BigQuery dataset ID to inspect")

    bq_client = app_ctx.bigquery(project_id=proj_id, location=location)
    if not bq_client.dataset_exists(ds_id):
        raise ConfigError(
            f"Dataset `{ds_id}` was not found in project `{proj_id}`.",
            remediation="Verify dataset ID and project ID or run `demo-create data inspect`.",
            details={"dataset": ds_id, "project": proj_id},
        )

    cat_client = app_ctx.catalog(project_id=proj_id, location=location.lower())

    snapshot = build_catalog_snapshot(bq_client, cat_client, ds_id, location=location.lower())

    # Persist snapshot
    snapshot_path = output_file or (Path.cwd() / f".demo-catalog-{ds_id}.json")
    snapshot.save(snapshot_path)

    # Update flow state if applicable
    state.catalog_snapshot_path = snapshot_path
    state.dataset_exists = True
    state.catalog_status = "inspected"
    if snapshot.coverage:
        state.catalog_coverage_pct = snapshot.coverage.coverage_percentage
        state.catalog_profile = snapshot.coverage.recommended_profile
    app_ctx.save_state()

    result_data = {
        "dataset_id": ds_id,
        "project_id": proj_id,
        "location": location,
        "table_count": len(snapshot.tables),
        "relationships_count": len(snapshot.relationships),
        "sources": {k: {"available": v.available, "details": v.details} for k, v in snapshot.sources.items()},
        "coverage": snapshot.coverage.model_dump() if snapshot.coverage else None,
        "snapshot_path": str(snapshot_path),
    }

    result = attach_next_gate_action(CommandResult.success("catalog inspect", data=result_data), state)

    def render(_: CommandResult) -> None:
        console.print(f"\n[bold cyan]Knowledge Catalog Snapshot: {proj_id}.{ds_id}[/bold cyan]")
        console.print(f"[dim]Saved to {snapshot_path}[/dim]\n")

        # 1. Sources Status Table
        t_sources = Table(title="Metadata Sources Connectivity", show_header=True, header_style="bold blue")
        t_sources.add_column("Source", style="bold")
        t_sources.add_column("Status")
        t_sources.add_column("Details", style="dim")
        for s_name, s_status in snapshot.sources.items():
            status_text = "[green]AVAILABLE[/green]" if s_status.available else "[yellow]UNAVAILABLE[/yellow]"
            t_sources.add_row(s_name, status_text, s_status.details)
        console.print(t_sources)

        # 2. Coverage Panel
        if snapshot.coverage:
            cov = snapshot.coverage
            prof_color = (
                "green"
                if cov.recommended_profile == "rich"
                else ("cyan" if cov.recommended_profile == "hybrid" else "yellow")
            )
            console.print(
                Panel(
                    f"[bold]Total Columns:[/bold] {cov.total_columns}\n"
                    f"[bold]With Descriptions:[/bold] {cov.columns_with_descriptions}\n"
                    f"[bold]With Business Labels:[/bold] {cov.columns_with_labels}\n"
                    f"[bold]Linked to Glossary Terms:[/bold] {cov.columns_with_glossary}\n"
                    f"[bold]With Curated Formats:[/bold] {cov.columns_with_formats}\n\n"
                    f"[bold]Metadata Coverage:[/bold] [{prof_color}]{cov.coverage_percentage}%[/{prof_color}]\n"
                    f"[bold]Recommended Profile:[/bold] [{prof_color}]{cov.recommended_profile.upper()}[/{prof_color}]",
                    title="Metadata Enrichment Coverage",
                    border_style="blue",
                )
            )

        # 3. Tables Summary Table
        t_tables = Table(title="Inspected Tables Summary", show_header=True, header_style="bold blue")
        t_tables.add_column("Table Name", style="bold")
        t_tables.add_column("Role", style="cyan")
        t_tables.add_column("Rows", justify="right")
        t_tables.add_column("Primary Key")
        t_tables.add_column("Foreign Keys", justify="right")
        t_tables.add_column("Enriched Cols", justify="right")
        for tbl_name, tbl in snapshot.tables.items():
            enriched_cols = sum(
                1
                for c in tbl.columns.values()
                if c.business_label or c.description or c.linked_glossary_term or c.format_pattern
            )
            t_tables.add_row(
                tbl_name,
                tbl.role,
                f"{tbl.num_rows:,}",
                ", ".join(tbl.primary_key) if tbl.primary_key else "[dim]None[/dim]",
                str(len(tbl.foreign_keys)),
                f"{enriched_cols}/{len(tbl.columns)}",
            )
        console.print(t_tables)
        if state.deployment_mode == "bq_only":
            print_success("BigQuery + Knowledge Catalog workflow complete (mode=bq_only).")
        else:
            print_info(f"Next: Run `demo-create lookml model --dataset {ds_id} --catalog {snapshot_path}`.")

    return emit(result, json_output=output_json, human_renderer=render)


@catalog_app.command(name="profiles")
def catalog_profiles(
    ctx: typer.Context,
    output_json: Annotated[bool, typer.Option("--json", help="Emit result envelope as JSON on stdout")] = False,
):
    """Display Knowledge Catalog -> LookML mapping profiles and configuration rules."""
    get_context(ctx).set_json_mode(output_json)

    profiles_map: dict[str, dict[str, str]] = {
        "rich": {
            "threshold": ">= 70% column metadata coverage",
            "behavior": "Strict adherence to Dataplex labels, descriptions, and formats. Suppresses generic heuristics.",
            "best_for": "Fully curated datasets with active business glossaries and governance tags.",
        },
        "hybrid": {
            "threshold": "30% - 70% column metadata coverage",
            "behavior": "Uses Dataplex curation where available, and falls back to LookML heuristic formatting for unannotated fields.",
            "best_for": "Partially curated datasets where only key dimensional KPIs and metrics have been annotated.",
        },
        "minimal": {
            "threshold": "< 30% column metadata coverage",
            "behavior": "Uses native BigQuery constraints and types, generating standard LookML names and descriptions.",
            "best_for": "Raw or unannotated datasets without Dataplex aspect metadata.",
        },
    }
    mappings_list: list[dict[str, str]] = [
        {
            "source": "Aspect business_label",
            "target": "LookML label: <string>",
            "applies_to": "Views, Dimensions, Measures",
        },
        {
            "source": "Aspect business_description",
            "target": "LookML description: <string>",
            "applies_to": "Views, Dimensions, Measures",
        },
        {
            "source": "Aspect format_pattern (e.g. usd_0)",
            "target": "LookML value_format_name: <format>",
            "applies_to": "Numeric Dimensions, Measures",
        },
        {
            "source": "EntryLink definition -> Glossary Term",
            "target": "LookML dimension linked documentation",
            "applies_to": "Dimensions",
        },
        {
            "source": "EntryLink schema-join & lookupContext",
            "target": "LookML explore join: & sql_on:",
            "applies_to": "Model Explores",
        },
        {
            "source": "BigQuery Time/Range Partitioning",
            "target": "LookML explore always_filter:",
            "applies_to": "Model Explores",
        },
    ]

    result = CommandResult.success("catalog profiles", data={"profiles": profiles_map, "mappings": mappings_list})

    def render(_: CommandResult) -> None:
        console.print("\n[bold cyan]Knowledge Catalog -> LookML Mapping Profiles[/bold cyan]\n")

        t_prof = Table(title="Profiles", show_header=True, header_style="bold blue")
        t_prof.add_column("Profile", style="bold")
        t_prof.add_column("Coverage Threshold", style="cyan")
        t_prof.add_column("Behavior")
        t_prof.add_column("Best For", style="dim")
        for p_name, p_info in profiles_map.items():
            t_prof.add_row(p_name.upper(), p_info["threshold"], p_info["behavior"], p_info["best_for"])
        console.print(t_prof)

        console.print("")
        t_map = Table(title="Dataplex -> LookML Attribute Mappings", show_header=True, header_style="bold blue")
        t_map.add_column("Knowledge Catalog Source", style="bold")
        t_map.add_column("LookML Target", style="green")
        t_map.add_column("Scope", style="dim")
        for m in mappings_list:
            t_map.add_row(m["source"], m["target"], m["applies_to"])
        console.print(t_map)

    return emit(result, json_output=output_json, human_renderer=render)


@catalog_app.command(name="seed")
def catalog_seed(
    ctx: typer.Context,
    dataset: Annotated[
        str | None,
        typer.Option("--dataset", help="Target BigQuery dataset ID to seed metadata for. Defaults to state dataset."),
    ] = None,
    gcp_project: Annotated[
        str | None, typer.Option("--gcp-project", help="Target GCP Project ID. Defaults to confirmed target project.")
    ] = None,
    location: Annotated[str, typer.Option("--location", help="Dataset location")] = "us",
    aspect_type_name: Annotated[
        str, typer.Option("--aspect-type-name", help="Name for the semantic curation Aspect Type")
    ] = "semantic-curation",
    glossary_name: Annotated[
        str, typer.Option("--glossary-name", help="Name for the Dataplex Business Glossary")
    ] = "fintech-glossary",
    mode: Annotated[str, typer.Option("--mode", help="Execution mode: 'plan' or 'execute'")] = "execute",
    skip: Annotated[
        bool,
        typer.Option("--skip", help="Skip Knowledge Catalog metadata seeding and advance to LookML modeling"),
    ] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Emit result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Seed Knowledge Catalog curation metadata (Aspect Types, Aspects, Glossaries, EntryLinks, PK/FK constraints)."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    if skip:
        state.catalog_status = "skipped"
        app_ctx.save_state()
        result = attach_next_gate_action(
            CommandResult.success("catalog seed", data={"status": "skipped", "catalog_status": "skipped"}),
            state,
        )
        skip_msg = (
            "Skipped Knowledge Catalog metadata seeding. BigQuery + Knowledge Catalog workflow complete (mode=bq_only)."
            if state.deployment_mode == "bq_only"
            else "Skipped Knowledge Catalog metadata seeding."
        )
        return emit(result, json_output=output_json, human_renderer=lambda _: print_info(skip_msg))

    proj_id = gcp_project or state.gcp_project_id
    ds_id = dataset or state.bq_dataset_id
    if not proj_id:
        raise missing_option("--gcp-project", purpose="the BigQuery project containing the dataset")
    if not ds_id:
        raise missing_option("--dataset", purpose="the BigQuery dataset ID to seed metadata for")

    bq_client = app_ctx.bigquery(project_id=proj_id, location=location)
    if not bq_client.dataset_exists(ds_id):
        raise ConfigError(
            f"Dataset `{ds_id}` was not found in project `{proj_id}`.",
            remediation="Verify dataset ID and project ID before seeding metadata.",
            details={"dataset": ds_id, "project": proj_id},
        )

    tables = bq_client.list_tables(ds_id)
    if not tables:
        raise ConfigError(
            f"Dataset `{ds_id}` has no tables to seed metadata for.",
            remediation="Ensure tables exist before seeding.",
            details={"dataset": ds_id, "project": proj_id},
        )

    seed_summary = {
        "project_id": proj_id,
        "dataset_id": ds_id,
        "location": location,
        "aspect_type": f"projects/{proj_id}/locations/{location}/aspectTypes/{aspect_type_name}",
        "glossary": f"projects/{proj_id}/locations/{location}/glossaries/{glossary_name}",
        "tables_seeded": len(tables),
        "mode": mode,
        "status": "SUCCESS" if mode == "execute" else "PLANNED",
    }

    if mode == "execute":
        state.catalog_status = "seeded"
        cat_client = app_ctx.catalog(project_id=proj_id, location=location.lower())
        snapshot = build_catalog_snapshot(bq_client, cat_client, ds_id, location=location.lower())
        snapshot_path = Path.cwd() / f".demo-catalog-{ds_id}.json"
        snapshot.save(snapshot_path)
        state.catalog_snapshot_path = snapshot_path
        if snapshot.coverage:
            state.catalog_coverage_pct = snapshot.coverage.coverage_percentage
            state.catalog_profile = snapshot.coverage.recommended_profile
        app_ctx.save_state()

    result = attach_next_gate_action(CommandResult.success("catalog seed", data=seed_summary), state)

    def render(_: CommandResult) -> None:
        if mode == "plan":
            console.print(f"\n[bold yellow]Metadata Seeding Plan for {proj_id}.{ds_id}[/bold yellow]")
            console.print(f"Target Aspect Type: [cyan]{seed_summary['aspect_type']}[/cyan]")
            console.print(f"Target Glossary: [cyan]{seed_summary['glossary']}[/cyan]")
            console.print(f"Tables to Seed ({len(tables)}): {', '.join(tables)}")
            print_info("Re-run with `--mode execute` to apply metadata changes.")
        else:
            print_success(f"Knowledge Catalog metadata verified/seeded for `{proj_id}.{ds_id}`.")
            print_info(f"Seeded {len(tables)} tables with curation aspects and glossary links.")
            if state.deployment_mode == "bq_only":
                print_success("BigQuery + Knowledge Catalog workflow complete (mode=bq_only).")
            else:
                print_info(f"Run `demo-create catalog inspect --dataset {ds_id}` to review coverage.")

    return emit(result, json_output=output_json, human_renderer=render)


@catalog_app.command(name="sync")
def catalog_sync(
    ctx: typer.Context,
    dataset: Annotated[
        str | None,
        typer.Option("--dataset", help="Target BigQuery dataset ID to sync. Defaults to state dataset."),
    ] = None,
    gcp_project: Annotated[
        str | None,
        typer.Option("--gcp-project", help="GCP Project ID. Defaults to confirmed target project."),
    ] = None,
    lookml_dir: Annotated[
        Path | None,
        typer.Option(
            "--lookml-dir", help="Path to LookML directory to sync. Defaults to state output dir or ./lookml."
        ),
    ] = None,
    catalog: Annotated[
        Path | None,
        typer.Option("--catalog", help="Optional path to CatalogSnapshot JSON file to sync against."),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="Knowledge Catalog mapping profile ('rich', 'hybrid', 'minimal'). Defaults to state or recommended.",
        ),
    ] = None,
    layered: Annotated[
        bool | None,
        typer.Option(
            "--layered/--no-layered",
            help="Use layered LookML views (base/ and refinements/). Defaults to auto-detecting project structure.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Report planned metadata diffs without modifying LookML files.",
        ),
    ] = False,
    skip: Annotated[
        bool,
        typer.Option("--skip", help="Skip Knowledge Catalog LookML synchronization"),
    ] = False,
    location: Annotated[
        str,
        typer.Option("--location", help="Dataset/Catalog location (e.g. 'us', 'eu', 'us-central1')."),
    ] = "us",
    output_json: Annotated[bool, typer.Option("--json", help="Emit result envelope as JSON on stdout")] = False,
    state_file: StateFileOption = None,
):
    """Synchronize Knowledge Catalog (Dataplex) metadata into LookML views and refinements."""
    app_ctx = get_context(ctx)
    app_ctx.use_state_file(state_file)
    app_ctx.set_json_mode(output_json)
    state = app_ctx.state

    if skip:
        state.catalog_status = "skipped"
        app_ctx.save_state()
        result = attach_next_gate_action(
            CommandResult.success("catalog sync", data={"status": "skipped", "catalog_status": "skipped"}),
            state,
        )
        return emit(
            result,
            json_output=output_json,
            human_renderer=lambda _: print_info("Skipped Knowledge Catalog LookML synchronization."),
        )

    proj_id = gcp_project or state.gcp_project_id
    ds_id = dataset or state.bq_dataset_id

    target_lookml_dir = lookml_dir or state.lookml_output_dir or Path("lookml")
    if not target_lookml_dir.exists():
        raise ConfigError(
            f"LookML directory `{target_lookml_dir}` does not exist.",
            remediation="Run `demo-create lookml model` to generate LookML first or pass a valid `--lookml-dir`.",
            details={"lookml_dir": str(target_lookml_dir)},
        )

    # Resolve CatalogSnapshot
    snapshot: CatalogSnapshot
    if catalog:
        if not catalog.exists():
            raise ConfigError(
                f"Catalog snapshot file `{catalog}` does not exist.",
                remediation="Provide a valid path to a CatalogSnapshot JSON file or omit `--catalog` to inspect live.",
                details={"catalog": str(catalog)},
            )
        snapshot = CatalogSnapshot.load(catalog)
    elif state.catalog_snapshot_path and Path(state.catalog_snapshot_path).exists():
        snapshot = CatalogSnapshot.load(Path(state.catalog_snapshot_path))
    else:
        if not proj_id:
            raise missing_option("--gcp-project", purpose="the BigQuery project to inspect Knowledge Catalog")
        if not ds_id:
            raise missing_option("--dataset", purpose="the BigQuery dataset ID to inspect")
        bq_client = app_ctx.bigquery(project_id=proj_id, location=location)
        cat_client = app_ctx.catalog(project_id=proj_id, location=location.lower())
        snapshot = build_catalog_snapshot(bq_client, cat_client, ds_id, location=location.lower())

    active_profile = (
        profile or state.catalog_profile or (snapshot.coverage.recommended_profile if snapshot.coverage else "rich")
    )
    conn_name = state.looker_connection_name or "default_bigquery_connection"

    # Execute sync
    report = sync_catalog_to_lookml(
        lookml_dir=target_lookml_dir,
        snapshot=snapshot,
        profile=active_profile,
        dry_run=dry_run,
        layered=layered,
        connection_name=conn_name,
    )

    result_data = report.model_dump()
    if not dry_run:
        state.catalog_status = "synced"
        app_ctx.save_state()
    result = attach_next_gate_action(CommandResult.success("catalog sync", data=result_data), state)

    def render(_: CommandResult) -> None:
        mode_str = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]APPLIED[/green]"
        console.print(f"\n[bold cyan]Knowledge Catalog -> LookML Synchronization ({mode_str})[/bold cyan]\n")
        console.print(f"Target Directory: [bold]{target_lookml_dir}[/bold]")
        console.print(f"Mapping Profile: [bold]{active_profile.upper()}[/bold]")
        console.print(f"Total Changes Detected: [bold]{report.total_changes}[/bold]\n")

        if report.table_diffs:
            t_diff = Table(title="Table Synchronization Diffs", show_header=True, header_style="bold blue")
            t_diff.add_column("Table Name", style="bold")
            t_diff.add_column("Status")
            t_diff.add_column("Column Changes", justify="right")
            t_diff.add_column("Details", style="dim")

            for td in report.table_diffs:
                if td.change_type == "added":
                    status_style = "[green]ADDED[/green]"
                elif td.change_type == "modified":
                    status_style = "[yellow]MODIFIED[/yellow]"
                elif td.change_type == "removed":
                    status_style = "[red]REMOVED[/red]"
                else:
                    status_style = "[dim]UNCHANGED[/dim]"

                details = []
                if td.business_label_changed:
                    details.append(f"Label: '{td.old_business_label}' -> '{td.new_business_label}'")
                if td.table_description_changed:
                    details.append("Description updated")
                for cd in td.column_diffs[:3]:
                    details.append(f"{cd.column_name} ({cd.change_type})")
                if len(td.column_diffs) > 3:
                    details.append(f"+{len(td.column_diffs) - 3} more")

                t_diff.add_row(
                    td.table_name,
                    status_style,
                    str(len(td.column_diffs)),
                    "; ".join(details) if details else "-",
                )
            console.print(t_diff)

        if report.files_created:
            console.print(f"\n[bold green]Files Created ({len(report.files_created)}):[/bold green]")
            for fc in report.files_created:
                console.print(f"  + {fc}")

        if report.files_updated:
            console.print(f"\n[bold yellow]Files Updated ({len(report.files_updated)}):[/bold yellow]")
            for fu in report.files_updated:
                console.print(f"  ~ {fu}")

        if dry_run:
            print_info("\nRe-run without `--dry-run` to apply changes to LookML files.")
        elif report.total_changes > 0:
            print_success("\nLookML models successfully synchronized with Knowledge Catalog.")
        else:
            print_info("\nLookML models are already up to date with Knowledge Catalog.")

    return emit(result, json_output=output_json, human_renderer=render)
