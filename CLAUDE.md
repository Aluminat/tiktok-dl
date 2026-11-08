# tiktok-dl

**Checks:** `uv run --with-requirements requirements-dev.txt pytest`, and the same with `ruff check .`. They run in the repo's own `.venv`; install packages only there, never into a global Python.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on Aluminat/tiktok-dl (gh CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
