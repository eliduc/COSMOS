from pathlib import Path

import uvicorn

# rest_api imports the JPype-based Python solver, which isn't available
# on Python 3.14 (no JPype wheel). Lazy-import inside main() instead so
# the package can still be imported by the Kotlin-engine benchmark scripts
# without timefold installed.

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGGING_CONF = PROJECT_ROOT / "logging.conf"


def main():
    from .rest_api import app  # noqa: F401  (registers FastAPI routes)
    config_kwargs = dict(
        app="flight_crew_scheduling.rest_api:app",
        host="127.0.0.1",
        port=8765,
        use_colors=True,
    )
    if LOGGING_CONF.is_file():
        config_kwargs["log_config"] = str(LOGGING_CONF)
    config = uvicorn.Config(**config_kwargs)
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
