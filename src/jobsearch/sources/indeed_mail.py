"""Indeed Postings from job alert mails, read over IMAP (ADR 0002).

Only mails from Indeed senders in the inbox are read. A mail is moved to the
processed folder only after its Postings are stored. A mail that yields no
Postings stays in the inbox and is reported, so a changed mail format shows up
instead of silently losing Jobs.

The parser is not built yet: it needs real sample mails (Phase 0). Until then
every alert mail yields nothing and stays in the inbox.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import parseaddr

from . import Posting

log = logging.getLogger(__name__)

IMAP_PORT = 993
_INDEED_DOMAIN = re.compile(r"(^|\.)indeed\.(com|de)$", re.IGNORECASE)
# Indeed also sends account mails: login codes, alert confirmations, profile changes. They are
# left alone in the inbox and not reported, since they never contain Jobs.
_ACCOUNT_MAIL = re.compile(
    # Whole phrases only: single words like "Account" or "Konto" also appear in job titles.
    r"mit dem folgenden code|bitte bestätigen sie|telefonnummer wurde|passwort wurde|e-mail-adresse wurde"
    r"|verification code|verify your|confirm your|password (was|has been)",
    re.IGNORECASE,
)


class MailError(Exception):
    """The mailbox could not be read. The message is German and meant for the user."""


@dataclass
class AlertMail:
    uid: str
    sender: str
    subject: str
    message: EmailMessage


def is_indeed(sender: str) -> bool:
    address = parseaddr(sender)[1]
    return bool(address) and bool(_INDEED_DOMAIN.search(address.rpartition("@")[2]))


def is_account_mail(subject: str) -> bool:
    return bool(_ACCOUNT_MAIL.search(subject))


def parse_alert(message: EmailMessage) -> list[Posting]:
    """Postings from one alert mail. Built in Phase 5 against real sample mails."""
    return []


class Mailbox:
    """The inbox over IMAP SSL. Use as a context manager."""

    def __init__(self, host: str, user: str, password: str, imap_factory=imaplib.IMAP4_SSL):
        self.host, self.user, self.password = host, user, password
        self.imap_factory = imap_factory
        self.imap = None

    def __enter__(self) -> Mailbox:
        try:
            self.imap = self.imap_factory(self.host, IMAP_PORT)
            self.imap.login(self.user, self.password)
            self._ok(self.imap.select("INBOX"), "Posteingang öffnen")
        except imaplib.IMAP4.error as e:
            raise MailError(f"Anmeldung bei {self.host} fehlgeschlagen. Stimmen IMAP_USER und IMAP_PASSWORD, "
                            f"und ist IMAP im Postfach erlaubt? ({e})") from None
        except OSError as e:
            raise MailError(f"Keine Verbindung zu {self.host}: {e}") from None
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.imap.logout()
        except Exception:  # logout after a broken connection is not worth an error
            pass

    @staticmethod
    def _ok(response, action: str):
        status, data = response
        if status != "OK":
            raise MailError(f"IMAP: {action} fehlgeschlagen ({data!r})")
        return data

    def indeed_mails(self) -> list[AlertMail]:
        """Alert mails from Indeed in the inbox, without account mails. BODY.PEEK leaves them unread."""
        uids = self._ok(self.imap.uid("SEARCH", None, '(FROM "indeed")'), "Suche")[0].split()
        mails = []
        for uid in uids:
            data = self._ok(self.imap.uid("FETCH", uid, "(BODY.PEEK[])"), "Abruf")
            raw = next((part[1] for part in data if isinstance(part, tuple)), None)
            if raw is None:
                continue
            message = email.message_from_bytes(raw, policy=default_policy)
            sender = str(message.get("From", ""))
            subject = str(message.get("Subject", ""))
            if is_account_mail(subject):
                log.debug("Skipping Indeed account mail: %s", subject)
            elif is_indeed(sender):
                mails.append(AlertMail(uid.decode(), sender, str(message.get("Subject", "")), message))
        return mails

    def move(self, uid: str, folder: str) -> None:
        """Move a mail, with COPY + delete as fallback for servers without MOVE."""
        quoted = '"' + folder.replace('"', '') + '"'
        if b"MOVE" in self.imap.capabilities or "MOVE" in self.imap.capabilities:
            self._ok(self.imap.uid("MOVE", uid, quoted), f"Verschieben nach {folder}")
            return
        self._ok(self.imap.uid("COPY", uid, quoted), f"Kopieren nach {folder}")
        self._ok(self.imap.uid("STORE", uid, "+FLAGS", r"(\Deleted)"), "Löschen markieren")
        self._ok(self.imap.expunge(), "Löschen")
