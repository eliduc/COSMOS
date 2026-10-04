"""A02: an answer to several rows cannot declare missing row effects complete."""
import copy
import json

import pytest

from cdpm import answers, family_params as fp, model as m, provenance, question_editions
from cdpm import render
from cdpm.export_task_package import derive_params_from_landing
from cdpm.gates import gate_answers
from test_cdpm_a03m_provenance import _doc, QID, PARAM


def _case(count=2, issued=False):
    doc = _doc()
    first = next(r for r in doc['rules'] if r['id'] == 'R-R')
    rows = [first]
    for index in range(2, count + 1):
        other = copy.deepcopy(first)
        other['id'] = f'R-R{index}'
        doc['rules'].append(other)
        rows.append(other)
    assert fp.ensure_family_param_questions(doc) == [QID]
    q = next(q for q in doc['questions'] if q['id'] == QID)
    if issued:
        question_editions.freeze(q)
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, q, rows


@pytest.mark.parametrize('count', [2, 3])
@pytest.mark.parametrize('issued', [False, True])
def test_only_processed_row_is_unblocked_and_consumer_cannot_lose_other_effects(count, issued):
    doc, q, rows = _case(count, issued)
    before = copy.deepcopy(rows)
    result = answers.apply_answers(doc, {QID: '600 минут'})
    assert result['applied'] == 0 and result['partial'] == 1 and result['awaiting'] == 0
    assert q['answer']['status'] == m.PARTIAL_ANSWER_STATUS
    assert QID not in rows[0].get('blockers', [])
    assert rows[1:] == before[1:]
    assert answers._affected_rules(doc, q) == rows[1:]
    for row in rows:
        projected = {p['name']: p['value'] for p in derive_params_from_landing(row)}
        assert projected.get(PARAM) == 600 or QID in row.get('blockers', [])
        assert row['template_fit']['params']['min_rest_home'] == before[0]['template_fit']['params']['min_rest_home']
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    assert provenance.package_problems(doc) == []


@pytest.mark.parametrize('issued', [False, True])
def test_sync_does_not_replace_partial_answer_with_code_closure(issued):
    doc, q, rows = _case(issued=issued)
    answers.apply_answers(doc, {QID: '600 минут'})
    saved = copy.deepcopy(q['answer'])
    for _ in range(2):
        fp.ensure_family_param_questions(doc)
        assert q['answer'] == saved
        assert m.is_open_question(q, doc=doc)
        assert QID in rows[1]['blockers']


def test_common_code_closure_guard_keeps_unhandled_answer_rows_open():
    doc, q, _ = _case()
    answers.apply_answers(doc, {QID: '600 минут'})
    reason = m.code_close_refusal(doc, q)
    assert reason and 'частично' in reason and 'R-R2' in reason
    saved = copy.deepcopy(doc)
    with pytest.raises(m.CodeCloseRefused):
        m.close_question_by_code(doc, q, raw=fp.CODE_CLOSED_RAW, note='уже задано')
    assert doc == saved


def test_existing_worker_finishes_only_remaining_row_and_preserves_first():
    doc, q, rows = _case(issued=True)
    answers.apply_answers(doc, {QID: '600 минут'})
    first = copy.deepcopy(rows[0])
    patch = copy.deepcopy(rows[1])
    patch.pop('blockers', None)
    patch['template_fit']['params'][PARAM] = fp._answer_cell(q, PARAM, 600, '600 минут', '600 минут')
    def worker(prompt):
        assert answers._affected_rules(doc, q) == [rows[1]]
        return json.dumps({'patches': [{'question': QID, 'change': 'Значение во второй строке',
                                        'rules': [patch]}]}, ensure_ascii=False)
    result = answers.apply_worker_patches(doc, worker)
    assert result['worker_rejected'] == [] and result['worker_applied'] == 1
    assert rows[0] == first and q['answer']['status'] == 'применён'
    for row in rows:
        assert QID not in row.get('blockers', [])
        assert next(p['value'] for p in derive_params_from_landing(row) if p['name'] == PARAM) == 600
    assert provenance.package_problems(doc) == []


def test_partial_answer_reaches_gate_and_display_with_remaining_row(tmp_path):
    doc, q, _ = _case()
    answers.apply_answers(doc, {QID: '600 минут'})
    model_path, summary_path = tmp_path / 'model.json', tmp_path / 'summary.json'
    model_path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    summary_path.write_text(json.dumps({'revision': doc['meta']['revision']}), encoding='utf-8')
    issues, counts = gate_answers.check(model_path, summary_path)
    assert counts['partial'] == 1
    assert any(QID in issue and 'частично' in issue for issue in issues)
    text = render.render_svod(doc)
    assert 'Ответ применён частично' in text
    assert 'Остались без результата обработчика: R-R2' in text


def test_single_row_answer_still_completes_without_worker():
    doc, q, rows = _case(count=1)
    result = answers.apply_answers(doc, {QID: '600 минут'})
    assert result['applied'] == 1 and result['awaiting'] == 0
    assert q['answer']['status'] == 'применён' and QID not in rows[0].get('blockers', [])


def test_unconditional_decline_is_a_result_for_every_named_row():
    doc, q, rows = _case()
    result = answers.apply_answers(doc, {QID: 'Не применяется.'})
    assert result['applied'] == 1 and q['answer']['status'] == 'применён'
    assert all(QID not in row.get('blockers', []) for row in rows)
