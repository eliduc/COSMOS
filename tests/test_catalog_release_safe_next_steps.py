"""Post-release guidance must preserve the binding bytes approved at release.

Pure command-list checks: no release, Git command, stand or service is run.
The separate byte-order probe demonstrates why rebuilding approved bindings
is unsafe; these regressions cover the product instruction that suggested it.
"""

import pytest


@pytest.mark.parametrize("writer", [
    "vmp.bindings_build", "cdpm\\bindings_build.py", "vmp.stand_tasks",
    "cdpm.stand_tables",
])
def test_release_does_not_prescribe_rebuilding_approved_evidence(writer):
    from catalog_service import releases

    commands = [step for step in releases.next_steps("0.7.0")
                if step.startswith(".venv")]
    assert not any(writer in step for step in commands), (
        "Release has already frozen support.json with the raw bindings SHA; "
        f"a post-release command must not regenerate that evidence: {writer}"
    )


def test_release_keeps_projection_commit_and_read_only_checks():
    from catalog_service import assemble, releases

    steps = releases.next_steps("0.7.0")
    commands = [step for step in steps if step.startswith(".venv")]
    assert steps[0].startswith(assemble.REFRESH_COMMAND)
    assert steps[1].startswith('git add catalog && git commit -m "каталог: выпуск 0.7.0"')
    assert any("vmp.bindings_gate" in command for command in commands)
    assert commands[-1].startswith(assemble.PARITY_COMMAND)
    assert "0 красных" in commands[-1]
    words = " | ".join(steps)
    assert "support.json" in words
    assert "до акта выпуска" in words
    assert "восстановите точные утверждённые артефакты" in words
    assert "база заморозки" in words


def test_draft_assembly_still_prescribes_stands_and_both_binding_builders():
    from catalog_service import assemble

    commands = assemble.NEXT_COMMANDS
    assert commands[0] == assemble.REFRESH_COMMAND
    assert commands[-1] == assemble.PARITY_COMMAND
    for writer in ("vmp.stand_tasks", "vmp.bindings_build", "cdpm.stand_tables",
                   "cdpm\\bindings_build.py", "vmp.bindings_gate"):
        assert any(writer in command for command in commands)
