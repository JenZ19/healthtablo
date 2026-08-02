#!/usr/bin/env python3
"""CLI семейного хаба здоровья.

Команды:
  hub.py init                                    — создать БД и папки
  hub.py ingest [path] [--subject s] [--source x] [--dry-run]
                                                   — разобрать файл или всю inbox/
  hub.py serve [--port 8765] [--host 127.0.0.1]   — поднять веб-интерфейс
  hub.py setpassword                              — задать логин и пароль для входа
  hub.py subjects                                 — список субъектов
  hub.py stats                                    — статистика по документам/результатам/маркерам
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hubcore import db as db_module  # noqa: E402
from hubcore import ingest as ingest_module  # noqa: E402


def cmd_init(args: argparse.Namespace) -> None:
    db_module.init_db()
    print(f"База данных создана: {db_module.DB_PATH}")
    print(f"Папка документов: {db_module.FILES_DIR}")
    print(f"Папка входящих файлов: {db_module.INBOX_DIR}")


def _print_result(r: ingest_module.IngestResult) -> None:
    label = {
        "ingested": "✓ разобран",
        "needs_review": "! требует разбора (субъект не определён)",
        "duplicate": "= дубликат, пропущен",
        "dry_run": "· сухой прогон",
        "error": "✗ ошибка",
    }.get(r.status, r.status)
    print(f"[{label}] {r.path}")
    if r.lab_name or r.doc_date:
        print(f"    лаборатория: {r.lab_name or '—'}, дата: {r.doc_date or '—'}, субъект: {r.subject_slug or '—'}")
    print(f"    результатов найдено: {r.results_count}, сопоставлено с маркерами: {r.matched_count}")
    if r.parse_note:
        print(f"    заметка парсера: {r.parse_note}")
    if r.message:
        print(f"    {r.message}")
    if r.status == "dry_run" and r.parsed:
        for res in r.parsed.results[:50]:
            val = res.value_num if res.value_num is not None else res.value_text
            ref = res.ref_text or ""
            print(f"      - {res.raw_name}: {val} {res.unit or ''}  (референс: {ref})")


def cmd_ingest(args: argparse.Namespace) -> None:
    db_module.init_db()
    if args.path:
        r = ingest_module.ingest_path(
            args.path, subject=args.subject, source=args.source, dry_run=args.dry_run
        )
        _print_result(r)
    else:
        results = ingest_module.ingest_inbox(subject=args.subject, source=args.source, dry_run=args.dry_run)
        if not results:
            print("В inbox/ ничего не найдено.")
            return
        for r in results:
            _print_result(r)
        ok = sum(1 for r in results if r.status == "ingested")
        review = sum(1 for r in results if r.status == "needs_review")
        dup = sum(1 for r in results if r.status == "duplicate")
        err = sum(1 for r in results if r.status == "error")
        print(f"\nИтого: {len(results)} файлов — разобрано {ok}, требует разбора {review}, дубликатов {dup}, ошибок {err}")


def cmd_serve(args: argparse.Namespace) -> None:
    db_module.init_db()
    import uvicorn

    from hubcore import auth as auth_module

    # Наружу — только с настроенным входом. Проверка до старта сервера,
    # чтобы «сначала подниму, потом закрою» не случилось никогда.
    auth_module.guard_public_bind(args.host)

    uvicorn.run("hubcore.web:app", host=args.host, port=args.port, reload=False)


def cmd_setpassword(args: argparse.Namespace) -> None:
    """Задать логин и пароль для входа.

    Пароль спрашивается интерактивно и не появляется ни в аргументах
    команды, ни в истории оболочки — оттуда его достаёт кто угодно с
    доступом к машине.
    """
    import getpass

    from hubcore import auth as auth_module

    username = (args.username or input("Логин: ")).strip()
    if not username:
        raise SystemExit("Логин не может быть пустым.")

    password = getpass.getpass("Пароль: ")
    if len(password) < 10:
        raise SystemExit("Пароль короче 10 знаков. Этот хаб может стоять в сети — возьмите длиннее.")
    if password != getpass.getpass("Пароль ещё раз: "):
        raise SystemExit("Пароли не совпали.")

    path = auth_module.save_config(username, password)
    print(f"Вход настроен. Логин: {username}")
    print(f"Файл: {path} (права 600, в репозиторий не попадает)")


def cmd_passwordoff(args: argparse.Namespace) -> None:
    """Убрать вход — осмысленно только для домашней машины."""
    from hubcore import auth as auth_module

    path = auth_module.config_path()
    if path.exists():
        path.unlink()
        print(f"Вход выключен, {path} удалён.")
    else:
        print("Вход и так не был настроен.")


def cmd_subjects(args: argparse.Namespace) -> None:
    db_module.init_db()
    with db_module.get_conn() as conn:
        rows = conn.execute("SELECT slug, name, kind, sex, species, breed FROM subjects ORDER BY id").fetchall()
        for row in rows:
            extra = row["species"] or row["kind"]
            print(f"{row['slug']:10s} {row['name']:15s} kind={row['kind']:6s} {extra or ''}")


def cmd_stats(args: argparse.Namespace) -> None:
    db_module.init_db()
    with db_module.get_conn() as conn:
        for row in conn.execute("SELECT slug, name FROM subjects ORDER BY id"):
            docs = conn.execute(
                "SELECT COUNT(*) c FROM documents WHERE subject_id=(SELECT id FROM subjects WHERE slug=?)",
                (row["slug"],),
            ).fetchone()["c"]
            results = conn.execute(
                "SELECT COUNT(*) c FROM results WHERE subject_id=(SELECT id FROM subjects WHERE slug=?)",
                (row["slug"],),
            ).fetchone()["c"]
            markers = conn.execute(
                "SELECT COUNT(DISTINCT analyte_id) c FROM results WHERE subject_id=(SELECT id FROM subjects WHERE slug=?) AND analyte_id IS NOT NULL",
                (row["slug"],),
            ).fetchone()["c"]
            print(f"{row['name']:15s} документов: {docs:4d}  результатов: {results:5d}  уникальных маркеров: {markers:3d}")
        unsorted_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE subject_id IS NULL").fetchone()["c"]
        print(f"{'Не разобрано':15s} документов: {unsorted_docs:4d}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Семейный хаб здоровья")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать БД и папки").set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="разобрать файл или всю inbox/")
    p_ingest.add_argument("path", nargs="?", default=None, help="путь к файлу (по умолчанию — вся inbox/)")
    p_ingest.add_argument("--subject", default=None, help="slug субъекта (me/husband/dog)")
    p_ingest.add_argument("--source", default="local", help="источник (invitro_lk/gmail/icloud/local/manual)")
    p_ingest.add_argument("--dry-run", action="store_true", help="только показать, что распозналось")
    p_ingest.set_defaults(func=cmd_ingest)

    p_serve = sub.add_parser("serve", help="поднять веб-интерфейс")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="адрес прослушивания; наружу — только с настроенным входом")
    p_serve.set_defaults(func=cmd_serve)

    p_pw = sub.add_parser("setpassword", help="задать логин и пароль для входа")
    p_pw.add_argument("--username", help="логин (иначе спросит)")
    p_pw.set_defaults(func=cmd_setpassword)

    sub.add_parser("passwordoff", help="выключить вход (только для домашней машины)").set_defaults(
        func=cmd_passwordoff)

    sub.add_parser("subjects", help="список субъектов").set_defaults(func=cmd_subjects)
    sub.add_parser("stats", help="статистика").set_defaults(func=cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
