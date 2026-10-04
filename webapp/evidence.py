"""Подписанное свидетельство проверки кандидата (пятый аудит, пакет C1).

Постановка. Кандидата проверяет Test, продвигает Stage, разворачивает
Prod. До сих пор Stage и Prod верили Test на слово: единственным следом
проверки была строка состояния в базе улучшений и файл с номером
ревизии. Оба лежат в контуре, до которого агент внедрения дотягивается —
он работает под той же учётной записью, что и приложение (см.
`isolation_problems`). Значит, «Test проверил» было утверждением без
доказательства: подделав состояние, кандидат получал раскат без единой
пройденной проверки.

Здесь — свидетельство, которое нельзя выписать себе самому:

* запись содержит РОВНО то, что проверялось: базовая ревизия, ревизия
  кандидата, отпечаток состава изменений и перечень пройденных проверок;
* запись подписана ключом, живущим ВНЕ дерева кандидата (по умолчанию в
  конфигурации работающего Prod) — кандидат его не читает и подделать
  подпись не может;
* Stage и Prod ПЕРЕСЧИТЫВАЮТ отпечаток изменений у себя и сверяют с
  подписанным. Совпало — действуют; не совпало или подписи нет — отказ.

Отпечаток состава берётся из `git diff --raw`: это перечень «режим,
объект, состояние» по каждому файлу, а не отрисованный текст различий.
Отрисовка зависит от версии git и настроек пользователя, перечень
объектов — нет.

Ключ создаётся при первом обращении. Ключ внутри дерева кандидата —
громкий отказ: подписывающий и подписываемый не могут быть одним лицом.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from .registry import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "webapp" / "config"
SCHEMA = "cosmos-evolution-evidence/v1"


class EvidenceError(RuntimeError):
    """Свидетельства нет, оно повреждено, подпись не сходится или запись
    описывает не ту ревизию. Во всех случаях действие отменяется."""


def key_path() -> Path:
    return Path(os.environ.get("COSMOS_EVIDENCE_KEY")
                or (CONFIG_DIR / "evolution-evidence.key"))


def evidence_path(imp_id: int, phase: str) -> Path:
    return CONFIG_DIR / f"evolution-evidence-{int(imp_id)}-{phase}.json"


def _assert_key_outside_candidate(path: Path) -> None:
    """Ключ обязан лежать вне деревьев, куда пишет кандидат."""
    from . import evolution_runner as er
    try:
        resolved = path.resolve()
    except OSError:                                       # noqa: PERF203
        resolved = path
    for name, tree in (("Test", er.test_dir()), ("Stage", er.stage_dir())):
        try:
            resolved.relative_to(Path(tree).resolve())
        except (ValueError, OSError):
            continue
        raise EvidenceError(
            f"ключ подписи лежит внутри дерева {name} ({resolved}) — "
            f"кандидат подписал бы сам себя; перенесите ключ вне рабочих "
            f"деревьев конвейера")


def _load_key() -> bytes:
    """Ключ подписи; создаётся при первом обращении.

    Права сужаются тем же средством, что и у ключа облачной модели. Если
    сузить не удалось — файл удаляется и работа прерывается: ключ,
    читаемый кем угодно, хуже отсутствующего, потому что создаёт
    видимость доказательства."""
    path = key_path()
    _assert_key_outside_candidate(path)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if len(raw) >= 32:
            return raw.encode()
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    try:
        from . import settings
        settings._restrict_windows_acl(path)
    except Exception as exc:                              # noqa: BLE001
        try:
            path.unlink()
        except OSError:
            pass
        raise EvidenceError(
            f"не удалось сузить права на ключ подписи: {exc}") from exc
    return key.encode()


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sign(body: dict) -> str:
    return hmac.new(_load_key(), _canonical(body), hashlib.sha256).hexdigest()


def diff_fingerprint(repo, base_sha: str, head_sha: str) -> str:
    """Отпечаток СОСТАВА изменений между двумя ревизиями.

    `--raw` даёт перечень объектов, а не отрисованный текст: он одинаков
    на разных версиях git и не зависит от настроек показа различий."""
    from . import evolution_runner as er
    # Через ЕДИНУЮ точку вызова контура (внешний аудит R9, P0-04): прежде
    # здесь был прямой вызов git без единого защитного флага, из-за чего
    # утверждение «механизмы git погашены на всех вызовах» было буквально
    # неверным — а этот вызов исполняется под записью подписанта и читает
    # дерево кандидата.
    code, out = er._git(Path(repo), "diff", "--raw", "--no-renames",
                        f"{base_sha}..{head_sha}", timeout_s=120)
    if code != 0:
        raise EvidenceError(
            f"не удалось прочитать состав изменений {base_sha[:10]}.."
            f"{head_sha[:10]}")
    lines = sorted(ln.strip() for ln in (out or "").splitlines() if ln.strip())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def issue(imp_id: int, phase: str, *, base_sha: str, candidate_sha: str,
          diff_hash: str, checks: dict) -> dict:
    """Выписать и сохранить свидетельство пройденной фазы."""
    body = {
        "schema": SCHEMA,
        "improvement_id": int(imp_id),
        "phase": phase,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "diff_hash": diff_hash,
        "checks": dict(checks),
    }
    record = {"body": body, "sig": sign(body)}
    path = evidence_path(imp_id, phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return record


def verify(imp_id: int, phase: str, *, candidate_sha: str | None = None,
           diff_hash: str | None = None) -> dict:
    """Прочитать свидетельство и проверить его. Любое несоответствие —
    исключение с объяснимой причиной, а не «False»: вызывающий обязан
    остановиться, и текст причины попадает в отчёт админу."""
    path = evidence_path(imp_id, phase)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceError(
            f"нет свидетельства фазы «{phase}» для улучшения №{imp_id}: "
            f"фаза не выполнялась либо её след удалён") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(
            f"свидетельство фазы «{phase}» нечитаемо: {exc}") from exc
    body = record.get("body")
    if not isinstance(body, dict) or not isinstance(record.get("sig"), str):
        raise EvidenceError("свидетельство повреждено: нет тела или подписи")
    if not hmac.compare_digest(sign(body), record["sig"]):
        raise EvidenceError(
            f"подпись свидетельства фазы «{phase}» не сходится — запись "
            f"подделана или выписана другим ключом")
    if body.get("schema") != SCHEMA:
        raise EvidenceError(f"чужая схема свидетельства: {body.get('schema')}")
    if int(body.get("improvement_id", -1)) != int(imp_id):
        raise EvidenceError("свидетельство выписано на другое улучшение")
    if candidate_sha and body.get("candidate_sha") != candidate_sha:
        raise EvidenceError(
            f"свидетельство выписано на ревизию "
            f"{str(body.get('candidate_sha'))[:12]}, а действие идёт над "
            f"{candidate_sha[:12]} — ветка переписана после проверки")
    if diff_hash and body.get("diff_hash") != diff_hash:
        raise EvidenceError(
            "состав изменений не совпадает с проверенным на предыдущей "
            "фазе: пересчитанный отпечаток отличается от подписанного")
    return body


def drop(imp_id: int) -> None:
    """Убрать свидетельства улучшения (перезапуск фазы Test)."""
    for phase in ("test", "stage"):
        try:
            evidence_path(imp_id, phase).unlink()
        except OSError:
            pass
