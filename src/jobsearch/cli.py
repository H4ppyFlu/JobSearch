"""Command line entry point: jobsearch init | status | daily | score."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import date

from . import config, db
from .companies import load_target_companies
from .config import Config, ConfigError
from .daily import run_daily
from .digest import write_digest
from .eligibility import Rules
from .notify import notify
from .scoring import backlog, run_scoring
from .sources.bundesagentur import BundesagenturClient


def _setup_logging(cfg: Config, verbose: bool) -> None:
    log_dir = cfg.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.FileHandler(log_dir / "jobsearch.log", encoding="utf-8")]
    stream = logging.StreamHandler()
    stream.setLevel(logging.DEBUG if verbose else logging.WARNING)
    handlers.append(stream)
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def cmd_init(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.private_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for example in (cfg.root / "private.example").iterdir():
        target = cfg.private_dir / example.name
        if not target.exists():
            shutil.copy(example, target)
            created.append(f"private/{example.name}")
    env = cfg.root / ".env"
    if not env.exists():
        shutil.copy(cfg.root / ".env.example", env)
        created.append(".env")
    db.connect(cfg.db_path).close()
    print(f"Datenbank bereit: {cfg.db_path}")
    if created:
        print("Angelegt aus den Beispielen, bitte ausfüllen: " + ", ".join(created))
    else:
        print("Private Dateien und .env waren schon vorhanden, nichts überschrieben.")


def cmd_status(cfg: Config) -> None:
    conn = db.connect(cfg.db_path)
    print("Jobs nach Eligibility:")
    for row in conn.execute("SELECT eligibility, count(*) n FROM jobs GROUP BY eligibility ORDER BY eligibility"):
        print(f"  {row['eligibility']:<11} {row['n']}")
    total = conn.execute("SELECT count(*) FROM postings").fetchone()[0]
    missing = conn.execute(
        """SELECT count(*) FROM postings p JOIN jobs j ON j.id = p.job_id
           WHERE p.description IS NULL AND j.eligibility != 'ineligible'"""
    ).fetchone()[0]
    print(f"Postings: {total} (ohne Beschreibung, aber in Reichweite: {missing})")
    tiers = dict(conn.execute("SELECT tier, count(*) FROM fit_scores GROUP BY tier").fetchall())
    print(f"Fit scores: ★★★ {tiers.get(3, 0)}, ★★ {tiers.get(2, 0)}, ★ {tiers.get(1, 0)}; "
          f"warten auf Bewertung: {backlog(conn)}")
    print("Letzte Läufe:")
    for row in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5"):
        state = {1: "ok", 0: "FEHLER", None: "läuft/abgebrochen"}[row["ok"]]
        print(f"  {row['started_at']}  {row['kind']:<11} {state}")
        if row["error"]:
            print(f"      {row['error']}")


def cmd_score(cfg: Config, conn, limit: int | None = None) -> int:
    """Score, write the Digest, show a toast. Returns 1 if the AI step failed."""
    counts = run_scoring(conn, cfg, limit)
    path, report = write_digest(conn, cfg.digests_dir, date.today(), counts)
    print(f"Bewertet: {counts.scored} (davon geringe Sicherheit: {counts.low_confidence}), "
          f"remote bestätigt/abgelehnt: {counts.remote_confirmed}/{counts.remote_rejected}, "
          f"warten noch: {counts.backlog}")
    print(f"Digest: {path}")
    notify(report, counts, path)
    if counts.error:
        print(f"Fehler bei der Bewertung: {counts.error}", file=sys.stderr)
        return 1
    return 0


def cmd_daily(cfg: Config, score: bool) -> int:
    cfg.require_home()
    terms = config.read_list_file(cfg.search_terms_path)
    if not terms:
        raise ConfigError(f"{cfg.search_terms_path} enthält keine Suchbegriffe.")
    rules = Rules(cfg.radius_km, cfg.radius_target_company_km,
                  tuple(config.read_list_file(cfg.exclusions_path)))
    targets = load_target_companies(cfg.companies_path)
    conn = db.connect(cfg.db_path)
    counts = run_daily(conn, cfg, BundesagenturClient(), targets, rules, terms)
    print(json.dumps({k: v for k, v in counts.__dict__.items() if k != "errors"}, ensure_ascii=False))
    for error in counts.errors:
        print(f"Fehler: {error}", file=sys.stderr)
    status = 1 if counts.errors else 0
    if score:
        status = max(status, cmd_score(cfg, conn))
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobsearch")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log auch auf der Konsole ausgeben")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Datenbank und private Dateien anlegen")
    sub.add_parser("status", help="Überblick über Datenbank und letzte Läufe")
    daily = sub.add_parser("daily", help="Daily run: Postings sammeln, Eligibility prüfen, bewerten")
    daily.add_argument("--no-score", action="store_true", help="Nur sammeln, nicht bewerten")
    score = sub.add_parser("score", help="Unbewertete Jobs bewerten, Digest schreiben")
    score.add_argument("--limit", type=int, help="Höchstens so viele Jobs (statt MAX_SCORED_PER_RUN)")
    args = parser.parse_args(argv)

    try:
        cfg = config.load()
        _setup_logging(cfg, args.verbose)
        if args.command == "init":
            cmd_init(cfg)
        elif args.command == "status":
            cmd_status(cfg)
        elif args.command == "daily":
            return cmd_daily(cfg, score=not args.no_score)
        elif args.command == "score":
            return cmd_score(cfg, db.connect(cfg.db_path), args.limit)
    except ConfigError as e:
        print(f"Konfiguration: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
