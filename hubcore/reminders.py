"""Из чего складываются напоминания.

Правило одно и оно жёсткое: напоминание строится из того, что в базе уже
есть, и ни о чём не догадывается. Хаб не решает, когда пора к врачу, —
он замечает, что срок, записанный в ваших же документах, подошёл.

Чтобы уведомления не превратились в шум, который перестают читать:

- каждое отправляется один раз, повторы отсекаются по ключу в push_sent;
- за один заход уходит не больше трёх сообщений;
- в тексте нет ни диагнозов, ни значений показателей — уведомление видно
  на заблокированном экране и через плечо.
"""

from __future__ import annotations

import datetime as dt

# Интервалы профилактики — общепринятые, те же, что показывает профиль.
PROPHYLAXIS_INTERVALS = {
    "вакцинация": 365,
    "бешенство": 365,
    "глисты": 90,
    "паразиты": 90,
}
SOON_DAYS = 7          # за сколько дней предупреждать о профилактике
CYCLE_AHEAD = 2        # за сколько дней предупреждать об ожидаемой менструации
CYCLE_LATE = 3         # с какой задержки сказать о ней
MAX_PER_RUN = 3        # больше трёх за раз — уже поток, а не напоминание


def _subject_names(conn) -> dict[int, tuple[str, str]]:
    return {
        r["id"]: (r["name"].split()[0], r["slug"])
        for r in conn.execute("SELECT id, name, slug FROM subjects")
    }


def pending_doses(conn, today: dt.date) -> list[dict]:
    """Курсы, которые идут сегодня, но сегодняшняя доза не отмечена."""
    names = _subject_names(conn)
    out = []
    rows = conn.execute(
        """SELECT m.id, m.subject_id, m.name, m.started, m.ended
             FROM medications m
            WHERE (m.started IS NULL OR m.started <= ?)
              AND (m.ended   IS NULL OR m.ended   >= ?)""",
        (today.isoformat(), today.isoformat()),
    ).fetchall()
    for m in rows:
        taken = conn.execute(
            "SELECT COUNT(*) n FROM med_doses WHERE medication_id=? AND date=? AND taken=1",
            (m["id"], today.isoformat()),
        ).fetchone()["n"]
        if taken:
            continue
        who, slug = names.get(m["subject_id"], ("", ""))
        out.append({
            "kind": "доза",
            "key": f"{m['id']}:{today.isoformat()}",
            "title": f"{who} — таблетки",
            "body": f"{m['name']}: на сегодня отметки нет",
            "url": f"/s/{slug}/meds",
        })
    return out


def prophylaxis_due(conn, today: dt.date) -> list[dict]:
    """Обработки и прививки: просроченные и те, что скоро."""
    names = _subject_names(conn)
    out = []
    rows = conn.execute(
        """SELECT subject_id, kind, MAX(date) AS last
             FROM prophylaxis GROUP BY subject_id, kind"""
    ).fetchall()
    for r in rows:
        interval = PROPHYLAXIS_INTERVALS.get(r["kind"])
        if not interval or not r["last"]:
            continue
        try:
            last = dt.date.fromisoformat(r["last"][:10])
        except ValueError:
            continue
        due = last + dt.timedelta(days=interval)
        left = (due - today).days
        if left > SOON_DAYS:
            continue
        who, slug = names.get(r["subject_id"], ("", ""))
        when = "просрочено" if left < 0 else ("сегодня" if left == 0 else f"через {left} дн.")
        out.append({
            "kind": "профилактика",
            # Ключ с датой срока, а не с сегодняшней: пока срок один и тот
            # же, напоминание уходит однажды, а не каждый день до него.
            "key": f"{r['subject_id']}:{r['kind']}:{due.isoformat()}",
            "title": f"{who} — {r['kind']}",
            "body": f"по сроку {when}",
            "url": f"/s/{slug}",
        })
    return out


def cycle_due(conn, today: dt.date) -> list[dict]:
    """Ожидаемая менструация и задержка — только по своим же отметкам."""
    from .web import cycle_episodes, cycle_stats

    names = _subject_names(conn)
    out = []
    subject_ids = [r["subject_id"] for r in
                   conn.execute("SELECT DISTINCT subject_id FROM cycle_days")]
    for sid in subject_ids:
        rows = conn.execute(
            "SELECT * FROM cycle_days WHERE subject_id=? ORDER BY date", (sid,)
        ).fetchall()
        stats = cycle_stats(cycle_episodes(rows))
        if not stats or not stats.get("expected"):
            continue
        left = stats["days_to_expected"]
        who, slug = names.get(sid, ("", ""))
        if 0 <= left <= CYCLE_AHEAD:
            body = "ожидается сегодня" if left == 0 else f"ожидается через {left} дн."
        elif left <= -CYCLE_LATE:
            body = f"задержка {-left} дн. по прошлым циклам"
        else:
            continue
        out.append({
            "kind": "цикл",
            "key": f"{sid}:{stats['expected']}:{'до' if left >= 0 else 'после'}",
            "title": f"{who} — цикл",
            "body": body,
            "url": f"/s/{slug}/cycle",
        })
    return out


def collect(conn, today: dt.date | None = None) -> list[dict]:
    """Всё, о чём стоит напомнить сегодня, в порядке важности."""
    today = today or dt.date.today()
    # Порядок именно такой: просроченная профилактика важнее сегодняшней
    # таблетки, а прогноз цикла — наименее срочное из трёх.
    return prophylaxis_due(conn, today) + pending_doses(conn, today) + cycle_due(conn, today)


def run(conn, today: dt.date | None = None, dry_run: bool = False) -> list[dict]:
    """Отправить то, что ещё не отправляли. Возвращает ушедшее."""
    from . import push

    today = today or dt.date.today()
    sent = []
    for item in collect(conn, today):
        if len(sent) >= MAX_PER_RUN:
            break
        if push.already_sent(conn, item["kind"], item["key"]):
            continue
        if not dry_run:
            ok, _ = push.send_to_all(conn, item["title"], item["body"], item["url"])
            if not ok:
                continue          # некому — не помечаем отправленным
            push.note_sent(conn, item["kind"], item["key"])
        sent.append(item)
    if not dry_run:
        push.forget_old_sent(conn)
    return sent
