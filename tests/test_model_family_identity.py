"""Pure configured-name policy; no model service or independence proof."""
import pytest

from cdpm.providers import independence_problems, role_family


def layout(left, right):
    return {role: {"channels": {"cloud": {"provider": provider, "model": model}}}
            for role, (provider, model) in zip(("extractor", "second_reader"), (left, right))}


@pytest.mark.parametrize("model,expected", [
    ("qwen3.6:8b", "qwen"), ("qwen3.6:32b", "qwen"),
    ("qwen2.5:7b", "qwen"), ("llama3:8b", "llama"),
    ("llama3.1:70b-instruct", "llama"), ("Qwen/Qwen3.6-8B", "qwen"),
    ("gpt-4.1", "gpt"), ("gpt-4o", "gpt"),
    ("claude-sonnet-4", "claude"), ("claude-opus-4", "claude"),
    ("gemini-2.5-pro", "gemini"), ("grok-4", "grok"),
])
def test_role_family_uses_lineage_prefix_not_size_or_version(model, expected):
    agents = layout(("local", model), ("local", "unrelated-test"))
    assert role_family(agents, "extractor") == ("local", expected)


@pytest.mark.parametrize("left,right", [
    (("local", "qwen3.6:8b"), ("openai", "qwen2.5:7b")),
    (("local", "llama3:8b"), ("local", "llama3:70b")),
    (("cloud", "claude-sonnet-4"), ("cloud", "claude-opus-4")),
])
def test_same_family_is_refused_across_providers_and_versions(left, right):
    assert independence_problems(layout(left, right))


@pytest.mark.parametrize("left,right", [
    (("local", "qwen3.6:8b"), ("local", "llama3:8b")),
    (("cloud", "claude-sonnet-4"), ("openai", "gpt-4.1")),
])
def test_distinct_declared_families_remain_usable(left, right):
    assert independence_problems(layout(left, right)) == []


def test_absent_role_and_existing_parent_default_are_preserved():
    assert role_family({}, "extractor") is None
    assert role_family({"worker": {"channels": {"cloud": {"provider": "cloud"}}}},
                       "extractor") == ("cloud", "claude")


@pytest.mark.parametrize("alias", ["opus", "sonnet", "haiku", "opus-4"])
def test_documented_cloud_aliases_do_not_create_an_independent_family(alias):
    assert independence_problems(layout(("cloud", alias), ("cloud", "claude-sonnet-4")))


@pytest.mark.parametrize("model,expected", [
    ("phi3:mini", "phi"), ("gemma2:9b", "gemma"),
    ("lab42/Example2:7b", "example"), ("example-v2", "example"),
])
def test_family_rule_is_shared_not_a_two_model_allowlist(model, expected):
    assert role_family(layout(("local", model), ("local", "other")), "extractor")[1] == expected


@pytest.mark.parametrize("model,expected", [("o1-preview", "o1"), ("o1-mini", "o1"), ("o3-mini", "o3")])
def test_single_letter_numeric_family_keeps_existing_generation_identity(model, expected):
    assert role_family(layout(("openai", model), ("cloud", "claude")), "extractor")[1] == expected


def test_o1_o3_existing_family_distinction_is_not_silently_removed():
    assert independence_problems(layout(("openai", "o1-preview"), ("openai", "o3-mini"))) == []
    assert independence_problems(layout(("openai", "o1-preview"), ("openai", "o1-mini")))
