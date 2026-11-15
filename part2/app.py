"""The localhost-only participant flow for the 14-day pilot."""

import secrets
import time
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from .protocol import DAYS, MAX_MESSAGE_LENGTH, SCHEDULE, SECONDS, TOPICS, TURNS
from .protocol import chat_turn, crisis_text, read_transcript
from .provider import BudgetExceeded, ProviderError
from .storage import PilotAlreadyRegistered, Storage

READER_MODES = ("cold", "history")


def study_day(today, previous):
    first = date.fromisoformat(previous[0]["local_day"]) if previous else today
    return (today - first).days + 1


def remove_local_report(report_path):
    if report_path is not None:
        Path(report_path).unlink(missing_ok=True)


def create_app(db_path, client, config, *, report_path=None, clock=time.time, dry_run=False):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=8_192,
    )
    store = Storage(db_path)
    timezone = ZoneInfo(config["timezone"])
    lock = RLock()

    def today():
        return datetime.fromtimestamp(clock(), timezone).date()

    def participant():
        participant_id = session.get("participant")
        if participant_id is None:
            return None
        if store.has_participant(participant_id):
            return participant_id
        session.pop("participant", None)
        return None

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return session["csrf"]

    def validate_post():
        token = request.form.get("csrf", "")
        if not secrets.compare_digest(csrf().encode(), token.encode()):
            abort(403)

    def show(screen, **values):
        return render_template("pilot.html", screen=screen, dry_run=dry_run, csrf=csrf(), **values)

    def home():
        return redirect(url_for("index"), code=303)

    def daily_session():
        participant_id = participant()
        if participant_id is None:
            return None
        return store.get_session(participant_id, today().isoformat())

    def is_current(form, row):
        return row is not None and form.get("day") == row["local_day"]

    def response_table(previous):
        saved = {
            row["day_number"]: (row, {guess["mode"]: guess for guess in store.guesses(row["id"])})
            for row in previous
        }
        return [
            saved.get(day, ({"day_number": day, "rating": None}, {}))
            for day in range(1, DAYS + 1)
        ]

    def finish_readers(row):
        session_id = row["id"]
        messages = store.messages(session_id)
        known = {guess["mode"] for guess in store.guesses(session_id)}
        earlier = store.earlier_transcripts(row["participant_id"], row["day_number"])
        for mode in READER_MODES:
            if mode in known:
                continue
            if not any(message["role"] == "user" for message in messages):
                store.save_guess(session_id, mode, error="empty_chat")
                continue
            # Claim the call before sending so a restart cannot bill it twice.
            store.save_guess(session_id, mode, error="interrupted")
            try:
                result = read_transcript(client, mode, messages, earlier, config["reader_output_tokens"])
                store.save_guess(session_id, mode, **result)
            except BudgetExceeded:
                store.save_guess(session_id, mode, error="budget")
            except ProviderError:
                store.save_guess(session_id, mode, error="provider")
            except (ValueError, OverflowError, RecursionError):
                store.save_guess(session_id, mode, error="invalid_response")
        store.transition(session_id, "readers", "done")

    @app.after_request
    def private_page(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; script-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    def index():
        with lock:
            participant_id = participant()
            if participant_id is None:
                return show("consent")
            day = today()
            previous = store.sessions(participant_id)
            day_number = study_day(day, previous)
            if day_number > DAYS:
                return show("reveal", results=response_table(previous), day_number=DAYS)
            row = store.get_session(participant_id, day.isoformat())
            if row is None:
                return show("ready", day_number=day_number)
            if row["phase"] == "readers":
                finish_readers(row)
                return home()
            if row["phase"] == "crisis":
                return show("crisis", day_number=day_number, reveal=day_number == DAYS)
            if row["phase"] == "done":
                if day_number == DAYS:
                    return show("reveal", results=response_table(previous), day_number=DAYS)
                return show("done", day_number=day_number)
            if row["phase"] == "chat" and clock() - row["started_at"] >= SECONDS:
                store.transition(row["id"], "chat", "rating")
                return home()
            messages = store.messages(row["id"])
            if row["phase"] == "rating":
                return show("chat_ended", row=row, messages=messages)
            return show(
                "chat", row=row, messages=messages, turns=sum(m["role"] == "user" for m in messages),
                seconds=max(0, SECONDS - (clock() - row["started_at"])), turn_limit=TURNS,
                max_length=MAX_MESSAGE_LENGTH,
            )

    @app.post("/register")
    def register():
        validate_post()
        if request.form.get("consent") != "yes":
            flash("Confirm consent to begin.")
            return home()
        with lock:
            if participant() is not None:
                return home()
            try:
                participant_id, code = store.register(clock())
            except PilotAlreadyRegistered:
                flash("This pilot already has a participant. Enter your access code to continue.")
                return home()
            session.clear()
            session["participant"] = participant_id
            return show("access_code", access_code=code)

    @app.post("/signin")
    def signin():
        validate_post()
        with lock:
            participant_id = store.participant_id(request.form.get("access_code", ""))
            if participant_id is None:
                flash("The access code did not match.")
                return home()
            session.clear()
            session["participant"] = participant_id
            return home()

    @app.post("/start")
    def start():
        validate_post()
        with lock:
            participant_id = participant()
            if participant_id is None:
                return home()
            day = today()
            day_number = study_day(day, store.sessions(participant_id))
            if 1 <= day_number <= DAYS:
                now = clock()
                topics = SCHEDULE[day_number - 1]
                row = store.start_session(participant_id, day.isoformat(), day_number, now, topics)
                if row["phase"] == "chat" and not store.messages(row["id"]):
                    store.add_message(row["id"], "assistant", TOPICS[topics[0]], now)
            return home()

    @app.post("/message")
    def message():
        validate_post()
        with lock:
            row = daily_session()
            if not is_current(request.form, row) or row["phase"] != "chat":
                return home()
            now = clock()
            messages = store.messages(row["id"])
            turn = sum(message["role"] == "user" for message in messages)
            if now - row["started_at"] >= SECONDS or turn >= TURNS:
                store.transition(row["id"], "chat", "rating")
                return home()
            if request.form.get("revision") != str(len(messages)):
                return home()
            content = request.form.get("message", "").strip()
            if not content or len(content) > MAX_MESSAGE_LENGTH:
                flash(f"Enter a message of up to {MAX_MESSAGE_LENGTH} characters.")
                return home()
            if crisis_text(content):
                store.transition(row["id"], "chat", "crisis")
                return home()
            store.add_message(row["id"], "user", content, now)
            messages = store.messages(row["id"])
            topic_index = min(len(row["topics"]) - 1, (turn + 1) * len(row["topics"]) // TURNS)
            try:
                situation, reply = chat_turn(
                    client, messages, row["topics"][topic_index], config["chat_output_tokens"],
                )
            except BudgetExceeded:
                store.transition(row["id"], "chat", "rating")
                flash("The free API quota is unavailable for another request. You can still record your rating.")
                return home()
            except (ProviderError, ValueError, OverflowError, RecursionError):
                store.transition(row["id"], "chat", "rating")
                flash("The model reply was unavailable. You can still record your rating.")
                return home()
            if situation == "crisis":
                store.transition(row["id"], "chat", "crisis")
                return home()
            now = clock()
            if now - row["started_at"] < SECONDS:
                store.add_message(row["id"], "assistant", reply, now)
            if now - row["started_at"] >= SECONDS or turn + 1 == TURNS:
                store.transition(row["id"], "chat", "rating")
            return home()

    @app.post("/end")
    def end():
        validate_post()
        with lock:
            row = daily_session()
            if is_current(request.form, row) and clock() - row["started_at"] >= SECONDS:
                store.transition(row["id"], "chat", "rating")
            return home()

    @app.get("/rating")
    def rating_page():
        with lock:
            row = daily_session()
            if row is None or row["phase"] != "rating":
                return home()
            return show("rating", row=row, anchor=row["day_number"] in (1, DAYS))

    @app.post("/rating")
    def rate():
        validate_post()
        with lock:
            row = daily_session()
            if not is_current(request.form, row) or row["phase"] != "rating":
                return home()
            value = request.form.get("rating", type=int)
            anchor = row["day_number"] in (1, DAYS)
            satisfaction = request.form.get("satisfaction", type=int) if anchor else None
            if value not in range(11) or (anchor and satisfaction not in range(11)):
                flash("Select a rating from 0 to 10 for each question.")
                return redirect(url_for("rating_page"), code=303)
            if store.save_rating(row["id"], value, satisfaction, clock()):
                finish_readers(row)
            return home()

    @app.get("/results")
    def results():
        with lock:
            participant_id = participant()
            if participant_id is None:
                return home()
            previous = store.sessions(participant_id)
            day_number = study_day(today(), previous)
            closed_day = any(
                row["day_number"] == DAYS and row["phase"] in ("done", "crisis") for row in previous
            )
            if day_number > DAYS or closed_day:
                return show("reveal", results=response_table(previous), day_number=DAYS)
            return home()

    @app.get("/delete")
    def delete_screen():
        if participant() is None:
            return home()
        return show("delete")

    @app.post("/delete")
    def delete():
        validate_post()
        with lock:
            code = request.form.get("access_code", "")
            participant_id = participant()
            if participant_id is None or store.participant_id(code) != participant_id:
                flash("Enter your matching access code to delete your data.")
                return redirect(url_for("delete_screen"), code=303)
            remove_local_report(report_path)
            store.delete_participant(code)
            session.clear()
            flash("Your local participant data was deleted.")
            return home()

    return app
