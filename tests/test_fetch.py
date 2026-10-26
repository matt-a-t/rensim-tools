"""rensim fetch: made-up messages from a fake IMAP server are filed under a temp data/raw just as
rensim add would, only from mail the GM's domain signed, without changing the mailbox."""
import contextlib
import io
import os
import ssl
import tempfile
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from renaissance import add, fetch
from renaissance.settings import RAW

from . import FIXTURES

JAN = FIXTURES / "downloads" / "tst_1547-01.txt"   # as the GM sends it, address and all
NEWS = RAW / "newsletters" / "NEWSL901"
SIGNED = "mx.example.com; dkim=pass header.i=@example.org header.s=s1; spf=pass smtp.mailfrom=example.org"
PASSWORD = "app-password-1234"


def message(*files: tuple[str, bytes], subject: str = "results", auth: str | None = SIGNED,
            sender: str = "gm@example.org", body: str = "") -> bytes:
    """A made-up email from the GM with these attachments."""
    msg = EmailMessage()
    if auth is not None:
        msg["Authentication-Results"] = auth
    msg["From"], msg["To"], msg["Subject"] = sender, "player@example.com", subject
    msg["Date"] = "Sat, 24 Oct 2026 22:10:33 +0000"
    msg.set_content(body or "Your results are attached.")
    for name, data in files:
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL, recording what it was asked."""
    instances: list["FakeIMAP"] = []
    messages: list[bytes] = []

    def __init__(self, host, ssl_context=None, timeout=None):
        self.host, self.ssl_context, self.calls = host, ssl_context, []
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user))
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset, *criteria):
        self.calls.append(("search", *criteria))
        return "OK", [b" ".join(str(n + 1).encode() for n in range(len(self.messages)))]

    def fetch(self, number, parts):
        self.calls.append(("fetch", number, parts))
        n = int(number)
        return "OK", [(f"{n} (BODY[] {{{len(self.messages[n - 1])}}}".encode(), self.messages[n - 1]), b")"]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b""]


class Fetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name) / "raw"
        FakeIMAP.instances, FakeIMAP.messages = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, *argv: str, password: str | None = PASSWORD) -> tuple[int, str]:
        environ = {k: v for k, v in os.environ.items() if k != "RENSIM_IMAP_PASSWORD"}
        if password is not None:
            environ["RENSIM_IMAP_PASSWORD"] = password
        out = io.StringIO()
        with patch.object(fetch, "RAW", self.raw), patch.object(fetch.imaplib, "IMAP4_SSL", FakeIMAP), \
                patch.dict(os.environ, environ, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return fetch.main(list(argv)), out.getvalue()

    def test_turn_and_newsletter_are_saved_named_from_inside(self):
        FakeIMAP.messages = [message(("tst.txt", JAN.read_bytes())),
                             message(("NEWSL901", NEWS.read_bytes()), subject="Newsletter #901")]
        code, out = self.main()
        self.assertEqual(code, 0, out)
        turn = self.raw / "turns" / "tst_1547-01.txt"
        self.assertEqual(turn.read_bytes(), (RAW / "turns" / JAN.name).read_bytes())
        self.assertFalse(add.has_address(turn.read_bytes()))
        self.assertEqual((self.raw / "newsletters" / "NEWSL901").read_bytes(), NEWS.read_bytes())
        self.assertIn("rensim run", out)

    def test_running_twice_saves_nothing_new(self):
        FakeIMAP.messages = [message(("tst.txt", JAN.read_bytes()), ("NEWSL901", NEWS.read_bytes()))]
        self.main()
        code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("already saved, identical"), 2)
        self.assertNotIn("rensim run", out)

    def test_other_attachments_are_skipped_not_failed(self):
        FakeIMAP.messages = [message(("Times_1.pdf", b"%PDF-1.7 ..."), ("NEWSL901", NEWS.read_bytes()))]
        code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertIn("Times_1.pdf: skipped, not a turn result or newsletter", out)

    def test_turn_pasted_in_the_message_text(self):
        FakeIMAP.messages = [message(body=JAN.read_bytes().decode("cp1252"))]
        self.main()
        self.assertTrue((self.raw / "turns" / "tst_1547-01.txt").exists())

    def test_different_saved_file_fails_unless_forced(self):
        dest = self.raw / "newsletters" / "NEWSL901"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"an older copy")
        FakeIMAP.messages = [message(("NEWSL901", NEWS.read_bytes()))]
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertIn("already exists with different content", out)
        self.assertEqual(self.main("--force")[0], 0)
        self.assertEqual(dest.read_bytes(), NEWS.read_bytes())

    def test_mailbox_is_read_only_and_searched_for_the_gm(self):
        FakeIMAP.messages = [message(("NEWSL901", NEWS.read_bytes()))]
        self.main("--since", "2026-10-01")
        calls = FakeIMAP.instances[0].calls
        self.assertIn(("login", "player@example.com"), calls)
        self.assertIn(('select', '"INBOX"', True), calls)
        self.assertIn(("search", "FROM", '"gm@example.org"', "SINCE", "1-Oct-2026"), calls)
        self.assertEqual({c[2] for c in calls if c[0] == "fetch"}, {"(BODY.PEEK[])"})
        self.assertEqual(calls[-1], ("logout",))

    def test_certificate_is_checked(self):
        self.main()
        context = FakeIMAP.instances[0].ssl_context
        self.assertEqual(FakeIMAP.instances[0].host, "imap.example.com")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_missing_password_stops_before_connecting(self):
        code, out = self.main(password=None)
        self.assertEqual(code, 2)
        self.assertIn("RENSIM_IMAP_PASSWORD", out)
        self.assertEqual(FakeIMAP.instances, [])

    def test_password_is_never_printed(self):
        FakeIMAP.messages = [message(("tst.txt", JAN.read_bytes())), message(auth=None)]
        _, out = self.main()
        self.assertNotIn(PASSWORD, out)

    def test_dry_run_saves_nothing(self):
        FakeIMAP.messages = [message(("tst.txt", JAN.read_bytes()))]
        code, out = self.main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would be saved as", out)
        self.assertFalse(self.raw.exists())

    def test_unsigned_or_forged_mail_is_skipped(self):
        turn = ("tst.txt", JAN.read_bytes())
        for auth in (None, "mx.example.com; dkim=fail header.i=@example.org",
                     "mx.example.com; dkim=pass header.i=@attacker.example",
                     "mx.example.com; dkim=pass header.i=@notexample.org"):
            with self.subTest(auth=auth):
                FakeIMAP.messages = [message(turn, auth=auth)]
                code, out = self.main()
                self.assertEqual(code, 0)
                self.assertIn("not signed by example.org", out)
                self.assertFalse(self.raw.exists())

    def test_only_the_top_authentication_results_header_counts(self):
        forged = EmailMessage()
        forged["Authentication-Results"] = "mx.example.com; dkim=none"
        forged["Authentication-Results"] = SIGNED   # added by the sender, below the server's
        self.assertFalse(fetch.signed_by(forged, "gm@example.org"))

    def test_dmarc_pass_and_subdomain_signatures_count(self):
        for auth in ("mx.example.com; dmarc=pass (p=REJECT) header.from=example.org",
                     "mx.example.com; dkim=pass header.d=mail.example.org"):
            msg = EmailMessage()
            msg["Authentication-Results"] = auth
            self.assertTrue(fetch.signed_by(msg, "gm@example.org"), auth)

    def test_no_verify_sender(self):
        FakeIMAP.messages = [message(("tst.txt", JAN.read_bytes()), auth=None)]
        self.main("--no-verify-sender")
        self.assertTrue((self.raw / "turns" / "tst_1547-01.txt").exists())

    def test_turn_without_a_findable_address_is_refused(self):
        no_copyright = JAN.read_bytes().replace(b"Copyright notice", b"Notice")
        FakeIMAP.messages = [message(("tst.txt", no_copyright))]
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertIn("address couldn't be found", out)
        self.assertFalse(self.raw.exists())
        self.assertEqual(self.main("--keep-address")[0], 0)

    def test_unsafe_account_is_refused(self):
        evil = JAN.read_bytes().replace(b"ACCOUNT CODE: TST", b"ACCOUNT CODE: ../../evil")
        FakeIMAP.messages = [message(("tst.txt", evil))]
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertEqual([p for p in Path(self.tmp.name).rglob("*") if p.is_file()], [])

    def test_oversized_attachment_is_skipped(self):
        FakeIMAP.messages = [message(("huge.txt", b"ACCOUNT CODE: " + b"x" * (fetch.MAX_ATTACHMENT + 1)))]
        code, out = self.main()
        self.assertEqual(code, 0)
        self.assertIn("huge.txt: skipped, larger than 2 MB", out)

    def test_control_characters_in_headers_are_not_printed(self):
        FakeIMAP.messages = [message(subject="results \x1b[2Jcleared")]
        _, out = self.main()
        self.assertNotIn("\x1b", out)

    def test_imap_date_ignores_the_locale(self):
        self.assertEqual(fetch.imap_date(date(2026, 3, 5)), "5-Mar-2026")


if __name__ == "__main__":
    unittest.main()
