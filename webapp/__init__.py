"""Pipeline web app (API contract v1, see webapp/API-CONTRACT.md).

FastAPI app `webapp.app:app` on port 8090: instance registry over the
canonical payloads, job orchestration (stage runs as subprocesses with
cwd = repo root and the venv python), results aggregation from the
final-report artifacts. The app never modifies the pipeline scripts.
"""
