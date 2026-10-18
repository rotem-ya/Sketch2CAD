"""Incoming e-mails (Outlook .msg, .eml): read them, find the document they are about, file them.

A reviewer's mail carries the request in its text ("replace the trench drain with the approved product...")
and the evidence in its attachments: a marked-up copy of the drawing, the original design, datasheets.
This module reads both, scores every document of every project against the mail, and files the mail and
its attachments under the chosen document. The API call itself is ai.revise: the app hands it the request
text from here (request_text) and the filed attachments.
"""
from __future__ import annotations

import html as html_lib
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from . import search
from .documents import Document, add_source, list_documents
from .projects import Project

MAIL_EXT = {".msg", ".eml"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
SEND_EXT = IMAGE_EXT | {".pdf", ".tif", ".tiff"}          # attachments the model can look at
# A line that starts the quoted, older part of a thread (Outlook and Gmail, English and Hebrew).
QUOTE_START = re.compile(r"^[ \t>]*(From:|-----Original Message-----|On .{3,160} wrote:|מאת:|_{6,}|-{6,})", re.M)
POINT = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*–])[ \t]+(\S.*)$")
WORD = re.compile(r"[0-9A-Za-z֐-׿]+")
STOP = {"the", "and", "for", "with", "from", "this", "that", "please", "your", "you", "are", "our", "attached",
        "find", "kindly", "regards", "best", "thanks", "thank", "dear", "comments", "review", "see", "below",
        "shop", "drawing", "drawings", "שלום", "תודה", "בברכה", "מצורף", "מצורפת", "מצורפים", "שרטוט"}
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass
class Attachment:
    name: str
    data: bytes
    mime: str = ""
    inline: bool = False            # a picture embedded in the mail body (signature logo), not a document

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass
class Mail:
    subject: str = ""
    sender: str = ""                # display name
    sender_email: str = ""
    to: str = ""
    cc: str = ""
    date: str = ""                  # "YYYY-MM-DD HH:MM" in this computer's time zone; "" when unknown
    body: str = ""                  # plain text of the whole thread
    attachments: list[Attachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def files(self) -> list[Attachment]:
        """Attachments that are documents (not the pictures embedded in the signature)."""
        return [a for a in self.attachments if not a.inline]

    @property
    def latest(self) -> str:
        """The newest message of the thread: the text above the first quoted mail."""
        return latest_message(self.body)


# ---------------------------------------------------------------------------------------------- reading
def read_mail(path: Path | str) -> Mail:
    """Read an Outlook .msg or an .eml file."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".msg":
        return _read_msg(path)
    if ext == ".eml":
        return read_eml(path.read_bytes())
    raise ValueError(f"Not an e-mail file (.msg or .eml): {path.name}")


def _safe_name(name: str, fallback: str) -> str:
    name = BAD_NAME.sub("_", name or "").strip(" .")
    return name or fallback


def _read_msg(path: Path) -> Mail:
    try:
        import olefile
    except ImportError as exc:
        raise RuntimeError("Reading Outlook .msg files needs the 'olefile' package: run install.bat "
                           "(or: pip install olefile).") from exc
    if not olefile.isOleFile(str(path)):
        raise ValueError(f"{path.name} is not an Outlook message file.")
    ole = olefile.OleFileIO(str(path))
    try:
        return mail_from_ole(ole)
    finally:
        ole.close()


def _ole_bytes(ole, name: str) -> bytes | None:
    return ole.openstream(name).read() if ole.exists(name) else None


def _ole_str(ole, tag: str, storage: str = "") -> str:
    """A string property: the Unicode stream (001F) or the 8-bit one (001E) of `tag` in `storage`."""
    base = f"{storage}/__substg1.0_{tag}" if storage else f"__substg1.0_{tag}"
    data = _ole_bytes(ole, base + "001F")
    if data is not None:
        return data.decode("utf-16-le", "replace").strip("\x00").strip()
    data = _ole_bytes(ole, base + "001E")
    if data is None:
        return ""
    for enc in ("utf-8", "cp1255", "latin-1"):
        try:
            return data.decode(enc).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
    return ""


def _ole_props(ole, storage: str, header: int) -> dict[int, bytes]:
    """{property tag: 8 raw value bytes} of the fixed-size properties in a __properties_version1.0 stream."""
    name = f"{storage}/__properties_version1.0" if storage else "__properties_version1.0"
    data = _ole_bytes(ole, name) or b""
    out = {}
    for off in range(header, len(data) - 15, 16):
        out[struct.unpack_from("<I", data, off)[0]] = data[off + 8:off + 16]
    return out


def _filetime(value: int) -> str:
    dt = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value // 10)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def mail_from_ole(ole) -> Mail:
    """Build a Mail from an open OLE compound file in the MSG layout (one stream per MAPI property)."""
    m = Mail(subject=_ole_str(ole, "0037"), sender=_ole_str(ole, "0C1A") or _ole_str(ole, "0042"),
             to=_ole_str(ole, "0E04"), cc=_ole_str(ole, "0E03"), body=_ole_str(ole, "1000"))
    for tag in ("5D01", "0C1F", "5D02", "0065"):                 # SMTP address first, X.500 addresses skipped
        addr = _ole_str(ole, tag)
        if "@" in addr:
            m.sender_email = addr
            break
    props = _ole_props(ole, "", 32)
    if not m.body:
        html = _ole_bytes(ole, "__substg1.0_10130102")
        if html:
            m.body = html_text(html.decode(_codepage(props), "replace"))
        else:
            m.warnings.append("The message text could not be read (RTF only); the attachments were read.")
    for pid in (0x0E06, 0x0039):                                  # delivery time, else submit time
        raw = props.get((pid << 16) | 0x0040)
        if raw:
            m.date = _filetime(struct.unpack("<Q", raw)[0])
            break
    storages = sorted({e[0] for e in ole.listdir(streams=True, storages=True)
                       if e[0].startswith("__attach_version1.0_")})
    for i, sto in enumerate(storages, 1):
        name = _safe_name(_ole_str(ole, "3707", sto) or _ole_str(ole, "3704", sto) or _ole_str(ole, "3001", sto),
                          f"attachment{i}")
        data = _ole_bytes(ole, f"{sto}/__substg1.0_37010102")
        if data is None:
            m.warnings.append(f"Attachment {name}: an embedded message or a link, not copied.")
            continue
        aprops = _ole_props(ole, sto, 8)
        flags = struct.unpack("<I", aprops.get((0x3714 << 16) | 0x0003, b"\0" * 8)[:4])[0]
        hidden = bool(aprops.get((0x7FFE << 16) | 0x000B, b"\0")[0])
        mime = _ole_str(ole, "370E", sto)
        picture = mime.startswith("image/") or Path(name).suffix.lower() in IMAGE_EXT
        inline = hidden or bool(flags & 4) or (bool(_ole_str(ole, "3712", sto)) and picture)
        m.attachments.append(Attachment(name=name, data=data, mime=mime, inline=inline))
    return m


def _codepage(props: dict[int, bytes]) -> str:
    raw = props.get((0x3FDE << 16) | 0x0003) or props.get((0x3FFD << 16) | 0x0003)
    if raw:
        cp = struct.unpack("<I", raw[:4])[0]
        if cp not in (65001, 20127, 0):
            try:
                "".encode(f"cp{cp}")
                return f"cp{cp}"
            except LookupError:
                pass
    return "utf-8"


def read_eml(data: bytes) -> Mail:
    msg = message_from_bytes(data, policy=policy.default)
    sender = next((a for a in getattr(msg["from"], "addresses", ()) if a.addr_spec), None) if msg["from"] else None
    if sender is not None:                                        # the parsed header copes with "Last, First"
        name, addr = sender.display_name, sender.addr_spec
    else:
        name, addr = parseaddr(str(msg.get("from", "")))
    m = Mail(subject=str(msg.get("subject", "")), sender=name or addr, sender_email=addr,
             to=str(msg.get("to", "")), cc=str(msg.get("cc", "")))
    if msg.get("date"):
        try:
            m.date = parsedate_to_datetime(str(msg["date"])).astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        text = part.get_content()
        m.body = html_text(text) if part.get_content_subtype() == "html" else text
    for i, part in enumerate(msg.iter_attachments(), 1):
        content = part.get_content()
        if isinstance(content, str):
            content = content.encode("utf-8")
        mime = part.get_content_type()
        aname = _safe_name(part.get_filename() or "", f"attachment{i}")
        if not isinstance(content, bytes):                        # an attached message (message/rfc822)
            m.warnings.append(f"Attachment {aname}: an embedded message, not copied.")
            continue
        inline = part.get_content_disposition() == "inline" or (part.get("Content-ID") is not None
                                                                 and mime.startswith("image/"))
        m.attachments.append(Attachment(name=aname, data=content, mime=mime, inline=inline))
    return m


def html_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", html)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", html))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ----------------------------------------------------------------------------------------------- text
def latest_message(body: str) -> str:
    """The newest message of a thread: the text above the first quoted mail ("From:", "On ... wrote:")."""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    for mt in QUOTE_START.finditer(text):
        head = text[:mt.start()].strip()
        if head:
            return head
    return text.strip()


def review_points(text: str) -> list[str]:
    """The numbered or bulleted items of a message ("1. Replace the trench drain with...")."""
    return [mt.group(1).strip() for mt in map(POINT.match, text.splitlines()) if mt]


def request_text(mail: Mail) -> str:
    """The revision request for the model: who asked, when, and the newest message of the thread."""
    who = " ".join(x for x in (mail.sender, f"<{mail.sender_email}>" if mail.sender_email else "") if x)
    head = f"Review comments received by e-mail from {who or 'the reviewer'}"
    if mail.date:
        head += f" on {mail.date[:10]}"
    if mail.subject:
        head += f', subject: "{mail.subject}"'
    parts = [head + ":", "", mail.latest or mail.body.strip()]
    if mail.files:
        parts += ["", "Attached to the mail: " + ", ".join(a.name for a in mail.files)]
    return "\n".join(parts)


def mail_text(mail: Mail) -> str:
    """Headers and the whole thread as plain text (search index, import page)."""
    head = [f"Subject: {mail.subject}", f"From: {mail.sender} {mail.sender_email}".strip(), f"Date: {mail.date}",
            f"To: {mail.to}", f"Cc: {mail.cc}", "Attachments: " + ", ".join(a.name for a in mail.files)]
    return "\n".join(head) + "\n\n" + mail.body


# ------------------------------------------------------------------------------------------- matching
@dataclass
class Match:
    project: Project
    document: Document
    score: float = 0.0
    reasons: list[dict] = field(default_factory=list)      # [{"en": ..., "he": ...}]


def _tokens(text: str) -> set[str]:
    words = {w.lower() for w in WORD.findall(text)}
    words |= {w.lower() for w in WORD.findall(search.hebrew_variants(text))}
    return {w for w in words if len(w) >= 3 and w not in STOP}


def match_document(mail: Mail, projects: list[Project], prefer: str | None = None) -> list[Match]:
    """Every document of every project, best match first.

    Scoring: the document's code, or one of its own files, named in the mail (certain); the project code in
    the mail (strong); words the mail shares with the document's title, description, tags and file names
    (the subject counts most, then attachment names, then the newest message) or with the project details.
    `prefer` (a project folder) breaks ties in favour of the open project.
    """
    names = " ".join(a.name for a in mail.files)
    latest = mail.latest
    haystack = f"{mail.subject}\n{names}\n{latest}".lower()
    weighted: dict[str, float] = {}
    for text, w in ((mail.subject, 3.0), (names, 2.0), (latest, 1.0)):
        for tok in _tokens(text):
            weighted[tok] = max(weighted.get(tok, 0.0), w)
    out: list[Match] = []
    for p in projects:
        p_tokens = _tokens(" ".join([p.code, p.name.get("en", ""), p.name.get("he", ""), p.client, p.location,
                                     p.contract_no]))
        code_hit = len(p.code) >= 4 and p.code.lower() in haystack
        preferred = bool(prefer) and str(Path(prefer).resolve()) == str(p.path.resolve())
        for d in list_documents(p):
            mt = Match(p, d)
            own = {Path(x).stem.lower() for x in list(d.sources) + list(d.outputs.values())}
            d_tokens = _tokens(" ".join([d.title.get("en", ""), d.title.get("he", ""), d.description, d.subject,
                                         " ".join(d.tags), " ".join(own), d.code.replace("-", " ")]))
            if d.code.lower() in haystack:
                mt.score += 50
                mt.reasons.append({"en": f"the document code {d.code} appears in the mail",
                                   "he": f"קוד המסמך {d.code} מופיע במייל"})
            for a in mail.files:
                stem = Path(a.name).stem.lower()
                if stem in own or stem.startswith(d.code.lower()):
                    mt.score += 30
                    mt.reasons.append({"en": f"the attachment {a.name} is a file of this document",
                                       "he": f"הקובץ המצורף {a.name} הוא קובץ של המסמך הזה"})
            if code_hit:
                mt.score += 10
                mt.reasons.append({"en": f"the project code {p.code} appears in the mail",
                                   "he": f"קוד הפרויקט {p.code} מופיע במייל"})
            shared = sorted(((w, t) for t, w in weighted.items() if t in d_tokens), reverse=True)
            if shared:
                mt.score += sum(w for w, _ in shared)
                words = ", ".join(t for _, t in shared[:6])
                mt.reasons.append({"en": f"words in common: {words}", "he": f"מילים משותפות: {words}"})
            mt.score += 0.5 * sum(w for t, w in weighted.items() if t in p_tokens and t not in d_tokens)
            if preferred:
                mt.score += 1
            out.append(mt)
    out.sort(key=lambda m: m.document.updated, reverse=True)
    out.sort(key=lambda m: -m.score)
    return out


# --------------------------------------------------------------------------------------------- filing
@dataclass
class Filed:
    mail: str                           # sources/<mail file>, relative to the document folder
    attachments: list[str]              # sources/<attachment>, in the order of mail.files


def file_mail(doc: Document, mail: Mail, raw: bytes, filename: str) -> Filed:
    """Save the mail file and its document attachments under the document's sources/ and index the text."""
    rel = add_source(doc, name=_safe_name(filename, "mail.msg"), data=raw)
    rels = [add_source(doc, name=a.name, data=a.data) for a in mail.files]
    search.store_content(doc, Path(rel).name, mail_text(mail))
    return Filed(mail=rel, attachments=rels)
