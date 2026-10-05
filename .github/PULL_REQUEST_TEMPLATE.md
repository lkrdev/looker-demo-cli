## Description
<!-- Briefly describe the changes introduced in this PR and why they are needed. -->

## Related Issues
<!-- Link any related issues, e.g. Closes #123 -->

## Contributor Checklist
Before requesting review, please confirm:
- [ ] Ran pre-commit hooks locally: `uv run pre-commit run --all-files` (or installed git hooks via `uv run pre-commit install`)
- [ ] Ran unit tests locally: `uv run pytest`
- [ ] Added unit tests covering new or modified functionality
- [ ] If CLI options, commands, or defaults changed: regenerated documentation via `uv run python scripts/gen_docs.py`
- [ ] Verified no sensitive credentials, Looker API keys, or GCP service account keys are committed
