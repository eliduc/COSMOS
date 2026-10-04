"""Exact prompt anchor syntax; pure offline source guards, no model calls."""
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp import source_check_compile as scc


@pytest.mark.parametrize('anchor,text,params', [
    ('абз.14', 'Базовый предел полётной смены — 12:00 (720 минут); превышение — только продлением.',
     {'event': 'duty_over_threshold', 'threshold_min': 720}),
    ('абз.18', 'Штраф 10 условных единиц за каждую ночёвку вне базы приписки.',
     {'event': 'layover_nights'}),
    ('абз.19', 'Надбавка за ночёвку вне базы, день начала — пятница или суббота.',
     {'event': 'layover_nights_in_window', 'days': ['friday', 'saturday']}),
])
def test_exact_prompt_brackets_preserve_supported_events(anchor, text, params):
    clauses = [{'sid': 'S1', 'anchor': anchor, 'text': text}]
    rendered = f'[S1:{anchor}]'
    assert rendered in scc.clauses_prompt(clauses)
    assert scc._soft_event_problem({'anchor': rendered, 'params': params}, clauses) is None


@pytest.mark.parametrize('anchor,sid', [
    ('[S1:p1', None), ('S1:p1]', None), ('[[S1:p1]]', None),
    ('[S1:p1] extra', None), ('[S9:p1]', None), ('[S1:p1]', 'S2'),
])
def test_bad_brackets_or_conflicting_source_remain_rejected(anchor, sid):
    clauses = [{'sid': 'S1', 'anchor': 'p1', 'text': 'Штраф за смену свыше 10 часов.'}]
    row = {'anchor': anchor, 'params': {'event': 'duty_over_threshold', 'threshold_min': 600}}
    if sid is not None:
        row['sid'] = sid
    assert scc._soft_source_text(row, clauses) is None


def test_bracket_does_not_disambiguate_duplicate_or_literal_collision():
    c = {'sid': 'S1', 'anchor': 'p1', 'text': 'Штраф за смену свыше 10 часов.'}
    row = {'anchor': '[S1:p1]'}
    assert scc._soft_source_text(row, [c, dict(c)]) is None
    other = {'sid': 'S2', 'anchor': '[S1:p1]', 'text': 'Кредитные часы за месяц.'}
    assert scc._soft_source_text(row, [c, other]) is None


def test_credit_domain_still_rejected_with_bracketed_anchor():
    c = {'sid': 'S1', 'anchor': 'p1', 'text': 'Штраф за кредитные часы свыше 10 часов за месяц.'}
    row = {'anchor': '[S1:p1]', 'params': {'event': 'duty_over_threshold', 'threshold_min': 600}}
    assert scc._soft_event_problem(row, [c]) is not None


def test_anchor_fix_changes_compile_cache_identity(monkeypatch):
    new = scc.compiler_fingerprint()
    monkeypatch.setattr(scc, 'SOFT_EVENT_GUARD_VERSION', 'source-domain-units/1')
    assert scc.compiler_fingerprint() != new


def test_reader_contract_names_canonical_weekday_values():
    for day in ('monday', 'tuesday', 'wednesday', 'thursday',
                'friday', 'saturday', 'sunday'):
        assert day in scc._KIND_CONTRACT
    assert 'сокращения' in scc._KIND_CONTRACT


def test_bad_calendar_reply_is_preserved_but_never_executed(monkeypatch):
    from webapp import llm_backend
    row = {'id': 'weekend', 'kind': 'soft_event_count', 'anchor': '[S1:p1]',
           'params': {'event': 'layover_nights_in_window',
                      'days': ['friday', 'saturday', 'sunday']},
           'note': 'wrong Sunday expansion'}
    monkeypatch.setattr(llm_backend, 'complete', lambda *a, **k:
                        {'ok': True, 'text': json.dumps([row]),
                         'backend': 'cloud', 'model': 'offline'})
    got = scc.compile_checks([{'sid': 'S1', 'anchor': 'p1',
                              'text': 'Ночёвки вне базы, день начала — пятница или суббота.'}],
                             scope='any', requested='cloud')
    assert len(got['problems']) == 1
    assert got['specs'][0]['kind'] == 'not_machine_checkable'
    assert got['specs'][0]['reader_candidate'] == row
    assert 'days' not in got['specs'][0]['params']
