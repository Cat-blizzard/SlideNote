import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run tests that need optional desktop software, live APIs, or external services.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(reason="needs --run-integration and optional external dependencies")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
