"""i18n parity guard (audit 2026-07-15): the five language blocks in
webapp/static/i18n.js must carry identical key sets and identical
{placeholder} sets per key, and every key referenced by the SPA
(t()/tf()/data-i18n/data-i18n-title/data-i18n-placeholder) must exist in
the ru block. This is the regression class the audit was asked to rule out,
currently prevented only by discipline — now enforced."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "webapp" / "static"
I18N = STATIC / "i18n.js"

LANGS = ("ru", "en", "uz", "kk", "ar")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _blocks():
    """Parse i18n.js into {lang: {key: value}} by walking each language
    block between its `  <lang>: {` opener and the matching close."""
    lines = I18N.read_text(encoding="utf-8").split("\n")
    out = {lang: {} for lang in LANGS}
    cur = None
    key_re = re.compile(r'^\s*"([^"]+)":\s*(".*")\s*,?\s*$')
    open_re = re.compile(r"^(" + "|".join(LANGS) + r"):\s*\{\s*$")
    for ln in lines:
        m = open_re.match(ln)
        if m:
            cur = m.group(1)
            continue
        if cur and re.match(r"^\}\s*,?\s*$", ln):
            cur = None
            continue
        if cur:
            km = key_re.match(ln)
            if km:
                out[cur][km.group(1)] = km.group(2)
    return out


def test_all_languages_have_identical_key_sets():
    blocks = _blocks()
    ref = set(blocks["ru"])
    assert len(ref) > 500, "i18n.js parse looks wrong"
    for lang in LANGS:
        keys = set(blocks[lang])
        missing = ref - keys
        extra = keys - ref
        assert not missing, f"{lang} is missing keys: {sorted(missing)[:10]}"
        assert not extra, f"{lang} has extra keys: {sorted(extra)[:10]}"


def test_no_duplicate_keys_within_a_language():
    """Дважды объявленный ключ побеждает последним объявлением и тихо
    подменяет первое. Разбор в словарь (`_blocks`) такую пару НЕ видит —
    поймано на себе 2026-08-13: правка словаря выполнилась дважды, и в
    каждом из пяти блоков оказалось по два `app.cvg.min`. Проверка идёт
    по СЫРЫМ строкам файла и печатает, сколько объявлений рассмотрела."""
    lines = I18N.read_text(encoding="utf-8").split("\n")
    key_re = re.compile(r'^\s*"([^"]+)":\s*".*"\s*,?\s*$')
    open_re = re.compile(r"^(" + "|".join(LANGS) + r"):\s*\{\s*$")
    cur, seen, dupes, total = None, {}, {}, 0
    for ln in lines:
        m = open_re.match(ln)
        if m:
            cur = m.group(1)
            seen[cur] = set()
            continue
        if cur and re.match(r"^\}\s*,?\s*$", ln):
            cur = None
            continue
        if cur:
            km = key_re.match(ln)
            if km:
                total += 1
                if km.group(1) in seen[cur]:
                    dupes.setdefault(cur, []).append(km.group(1))
                seen[cur].add(km.group(1))
    print(f"key declarations examined: {total} in {len(seen)} blocks")
    assert total > 2500, "i18n.js parse looks wrong"
    assert not dupes, f"duplicate key declarations: {dupes}"


def test_placeholders_match_across_languages():
    blocks = _blocks()
    for key, ru_val in blocks["ru"].items():
        ru_ph = set(_PLACEHOLDER.findall(ru_val))
        for lang in LANGS:
            val = blocks[lang].get(key, "")
            assert set(_PLACEHOLDER.findall(val)) == ru_ph, (
                f"{lang}/{key}: placeholder set differs from ru")


def test_no_duplicate_tab_labels_per_language():
    """EN/AR shipped two tabs both labelled 'Runs' (audit 2026-07-15). The
    visible tab labels within a language must be distinct."""
    blocks = _blocks()
    tab_keys = [k for k in blocks["ru"] if k.startswith("html.tab.")]
    for lang in LANGS:
        labels = [blocks[lang][k] for k in tab_keys]
        dupes = {x for x in labels if labels.count(x) > 1}
        assert not dupes, f"{lang} has duplicate tab labels: {dupes}"


def test_referenced_keys_exist_in_ru():
    blocks = _blocks()
    ru = set(blocks["ru"])
    key_lit = re.compile(r'\bt[f]?\(\s*"([^"]+)"')
    dom_attr = re.compile(
        r'data-i18n(?:-title|-placeholder)?="([^"]+)"')
    # Ревью 2026-07-19: ключи, приходящие в t() через МАССИВЫ полей
    # (renderKv/MR_COMPARE_ROWS: ["field", "app.f.xxx"]), literal-регексом
    # не видны — опечатка уехала бы молча. Любой строковый литерал вида
    # "app.xxx.yyy"/"html.xxx.yyy" в JS обязан существовать в ru.
    bare_key = re.compile(r'"((?:app|html)\.[A-Za-z0-9_.]+)"')
    referenced = set()
    for name in ("app.js", "index.html", "demo.js", "onboarding.js"):
        p = STATIC / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        referenced |= set(key_lit.findall(txt))
        referenced |= set(dom_attr.findall(txt))
        if name.endswith(".js"):
            referenced |= set(bare_key.findall(txt))
    # Only assert on complete literal keys: dotted, no spaces, and NOT a
    # trailing-dot prefix (those are `t("app.ev." + code)` dynamic lookups).
    missing = {k for k in referenced
               if "." in k and " " not in k and not k.endswith(".")
               and k not in ru}
    assert not missing, f"keys referenced but absent in ru: {sorted(missing)}"
