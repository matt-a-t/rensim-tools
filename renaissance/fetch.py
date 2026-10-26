"""Save new turn results and GM newsletters straight from your mailbox into data/raw.

    rensim fetch [--days N | --since YYYY-MM-DD] [--dry-run] [--run] [--force] [--keep-address]

Reads the mail from the GM's address over IMAP, and saves every attachment that is a turn result
or a newsletter just as `rensim add` would: named from what's inside, the address removed. Other
attachments (PDFs and so on) are skipped. A file already saved unchanged is left alone, so it's
safe to run on a schedule: running it twice saves nothing the second time.

The mailbox comes from the [fetch] table in rensim.toml:

    [fetch]
    sender = "gm@example.com"     # the GM's address
    user = "you@gmail.com"        # the mailbox to read (or RENSIM_IMAP_USER)
    host = "imap.gmail.com"       # the default
    mailbox = "INBOX"             # the default
    days = 45                     # how far back to look, by default

The password only comes from RENSIM_IMAP_PASSWORD (for Gmail, an app password); it's never read
from the file or printed. The mailbox is opened read-only: nothing is marked, moved or deleted.

Mail is only trusted if the receiving server says the GM's domain signed it (dkim=pass or
dmarc=pass in the top Authentication-Results header), because anyone can write the GM's address
in a From line. --no-verify-sender skips that check, for servers that don't add the header.
Unlike `rensim add`, a turn whose address can't be found is refused rather than saved as it is.
"""
from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import os
import re
import ssl
import sys
import tempfile
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from .add import finish, save
from .common import detect_kind
from .settings import FETCH, RAW

MAX_ATTACHMENT = 2_000_000   # bytes; a turn is about 25 KB, a newsletter less
MAX_MESSAGES = 200           # the most recent ones, if a search finds more
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()   # IMAP's, whatever the locale
DKIM_RE = re.compile(r"\bdkim=pass\b[^;]*?\bheader\.[di]=@?([\w.-]+)", re.I)
DMARC_RE = re.compile(r"\bdmarc=pass\b[^;]*?\bheader\.from=([\w.-]+)", re.I)


def imap_date(day: date) -> str:
    """date(2026, 10, 24) -> '24-Oct-2026', as IMAP SEARCH SINCE wants it."""
    return f"{day.day}-{MONTHS[day.month - 1]}-{day.year}"


def connect(host: str) -> imaplib.IMAP4_SSL:
    """An IMAP connection that checks the server's certificate (imaplib doesn't by default)."""
    return imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context(), timeout=60)


def search(client, sender: str, since: date) -> list[bytes]:
    """The raw messages from sender since that day, oldest first, read without marking them read."""
    _, found = client.search(None, "FROM", f'"{sender}"', "SINCE", imap_date(since))
    messages = []
    for number in found[0].split()[-MAX_MESSAGES:]:
        _, parts = client.fetch(number, "(BODY.PEEK[])")
        messages += [part[1] for part in parts if isinstance(part, tuple)]
    return messages


def signed_by(message: EmailMessage, sender: str) -> bool:
    """Whether the receiving server says sender's domain signed the message. Only the top
    Authentication-Results header counts: the server adds it, while lower ones came with the mail."""
    results = message.get_all("Authentication-Results") or []
    if not results:
        return False
    domain = sender.rpartition("@")[2].lower()
    signers = [d.lower() for d in DKIM_RE.findall(str(results[0])) + DMARC_RE.findall(str(results[0]))]
    return any(d == domain or d.endswith("." + domain) for d in signers)


def attachments(message: EmailMessage) -> list[tuple[str, bytes]]:
    """(name, bytes) for each attachment, plus a plain-text body that is itself a turn result."""
    found = [(part.get_filename() or "(unnamed)", part.get_payload(decode=True) or b"")
             for part in message.iter_attachments()]
    body = message.get_body(preferencelist=("plain",))
    if body is not None and body.get_content().lstrip().startswith("ACCOUNT CODE:"):
        found.append(("(message text)", body.get_content().encode("cp1252", errors="replace")))
    return found


def printable(text: str) -> str:
    """A header made safe to print: no control characters from someone else's mail."""
    return "".join(c if c.isprintable() else " " for c in text)


def fetch(messages: list[bytes], raw: Path, sender: str, force: bool = False,
          keep_address: bool = False, verify_sender: bool = True, write: bool = True) -> tuple[int, int]:
    """Save the turn results and newsletters in these messages under raw. Returns (saved, failed)."""
    saved = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for n, data in enumerate(messages):
            message = email.message_from_bytes(data, policy=email.policy.default)
            print()
            print(printable(f"{message['Date'] or '(no date)'}, {message['Subject'] or '(no subject)'}"))
            if verify_sender and not signed_by(message, sender):
                print(f"  skipped: not signed by {sender.rpartition('@')[2]} (--no-verify-sender reads it anyway)")
                continue
            try:
                found = attachments(message)
            except Exception as e:   # someone else's mail: a broken part shouldn't stop the rest
                print(f"  not read: {e}", file=sys.stderr)
                failed += 1
                continue
            for m, (name, content) in enumerate(found):
                label = printable(name)
                if len(content) > MAX_ATTACHMENT:
                    print(f"{label}: skipped, larger than {MAX_ATTACHMENT // 1_000_000} MB")
                    continue
                path = Path(tmp) / f"{n}-{m}"   # our name, never the attachment's
                path.write_bytes(content)
                if detect_kind(path) is None:
                    print(f"{label}: skipped, not a turn result or newsletter")
                    continue
                written = save(label, path, raw, force, keep_address, require_address=True, write=write)
                failed += written is None
                saved += bool(written)
    return saved, failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rensim fetch", description=__doc__.split("\n\n")[0])
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--days", type=int, default=int(FETCH["days"]), help="look back this many days (default %(default)s)")
    when.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD", help="look back to this day")
    ap.add_argument("--dry-run", action="store_true", help="say what would be saved, without saving it")
    ap.add_argument("--force", action="store_true", help="replace a saved file with different content")
    ap.add_argument("--keep-address", action="store_true", help="save turns with the mailing address still in")
    ap.add_argument("--no-verify-sender", action="store_true", help="read mail without checking who signed it")
    ap.add_argument("--run", action="store_true", help="then rensim run, if anything new was saved")
    args = ap.parse_args(argv)

    sender, user, password = FETCH["sender"], FETCH["user"], os.environ.get("RENSIM_IMAP_PASSWORD", "")
    missing = [what for what, value in (("[fetch] sender in rensim.toml", sender),
                                        ("[fetch] user in rensim.toml (or RENSIM_IMAP_USER)", user),
                                        ("RENSIM_IMAP_PASSWORD", password)) if not value]
    if missing:
        print(f"rensim fetch: set {', '.join(missing)}", file=sys.stderr)
        return 2
    since = args.since or date.today() - timedelta(days=args.days)

    try:
        client = connect(FETCH["host"])
    except (OSError, imaplib.IMAP4.error) as e:
        print(f"rensim fetch: couldn't connect to {FETCH['host']}: {e}", file=sys.stderr)
        return 1
    try:
        client.login(user, password)
        status, detail = client.select(f'"{FETCH["mailbox"]}"', readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error(f"no mailbox {FETCH['mailbox']!r} ({detail[0]!r})")
        messages = search(client, sender, since)
    except (OSError, imaplib.IMAP4.error) as e:
        print(f"rensim fetch: couldn't read {user}'s {FETCH['mailbox']} on {FETCH['host']}: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass

    print(f"{len(messages)} message(s) from {sender} since {since}")
    saved, failed = fetch(messages, RAW, sender, args.force, args.keep_address,
                          verify_sender=not args.no_verify_sender, write=not args.dry_run)
    return finish(0 if args.dry_run else saved, failed, args.run)


if __name__ == "__main__":
    sys.exit(main())
