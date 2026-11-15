"""Start the local pilot, check model access, delete data, or write the final report."""

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory

from .app import create_app, remove_local_report
from .provider import Client, ProviderError
from .storage import Storage

ROOT = Path(__file__).resolve().parents[1]
PILOT_DIR = ROOT / "data" / "pilot"
DATABASE = PILOT_DIR / "pilot.sqlite3"
REPORT = ROOT / "reports" / "part2.md"
PORT = 8765


def load_config():
    return json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))


class DryRunClient:
    """Predictable stand-in for exercising the local screens without the API."""

    def complete(self, purpose, system, messages, max_tokens, *, json_schema=None):
        if purpose == "chat":
            return json.dumps({"situation": "neutral", "reply": "Do you have a favorite example?"})
        return json.dumps({"guess": 5, "confidence": 0.25, "cues": ["Synthetic example"]})


def run_app(app):
    print(f"Open http://127.0.0.1:{PORT} on this computer.", flush=True)
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start an offline dry run or the live pilot.")
    serve.add_argument("--live", action="store_true", help="Use a token from a Cloudflare Workers Free account.")
    commands.add_parser("check-model", help="Check model access without an inference request.")
    commands.add_parser("delete-my-data", help="Remove the participant's local data using the access code.")
    commands.add_parser("report", help="Generate the local report once the pilot closes.")
    args = parser.parse_args()
    config = load_config()

    try:
        if args.command == "serve" and not args.live:
            PILOT_DIR.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="practice-", dir=PILOT_DIR) as temp:
                database = Path(temp) / "practice.sqlite3"
                run_app(create_app(database, DryRunClient(), config, dry_run=True))
            return 0

        if args.command in ("serve", "check-model"):
            credentials = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
            if not all(os.environ.get(name, "").strip() for name in credentials):
                parser.exit(
                    1,
                    "Live mode requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN "
                    "from a Workers Free account. The offline dry run is available.\n",
                )
            PILOT_DIR.mkdir(parents=True, exist_ok=True)
            client = Client(DATABASE, config)
            client.verify_model()
            print(f"Authenticated model list includes {config['model']}.", flush=True)
            if args.command == "serve":
                run_app(create_app(DATABASE, client, config, report_path=REPORT))
            return 0

        if args.command == "delete-my-data":
            code = getpass("Participant access code: ")
            storage = Storage(DATABASE)
            if storage.participant_id(code) is None:
                parser.exit(1, "The access code did not match.\n")
            remove_local_report(REPORT)
            if not storage.delete_participant(code):
                parser.exit(1, "The participant has already been deleted.\n")
            print("Deleted the participant's local data.")
            return 0

        for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
            os.environ[variable] = "1"
        from .analysis import PilotIncompleteError, write_report

        try:
            write_report(DATABASE, REPORT)
        except PilotIncompleteError as error:
            parser.exit(1, f"{error}\n")
        print(f"Wrote {REPORT}.")
        return 0
    except ProviderError as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
