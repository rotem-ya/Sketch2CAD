"""E-mail intake: reading .eml and .msg, the newest message and its points, matching the document, filing,
the revision request with the reviewer's attachments, and readable API errors."""
import io
import json
import struct
from datetime import datetime, timezone
from email.message import EmailMessage

import pytest
from test_features import _white_png, fake_client

from sketch2cad import mail, search
from sketch2cad.documents import Document, create_document
from sketch2cad.projects import create_project


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


SUBJECT = "Building P – Trench Drain Shop Drawing Review Comments"
BODY = """Hi Rotem,

Please find attached my review comments on the trench drain shop drawing for Building P.

Kindly address the following points:

1.\tSchematic Diagram: Replace the trench drain shown with the approved product (datasheet attached).
2.\tDimensions: Maintain the 1,200 mm vertical distance from Level ±0.00 to the top of the pipe.
3.\tOriginal Design: Please follow the original design.

Best regards,
Papis

From: Kande, Mamadou
Sent: Saturday, 29 August 2026 18:30
To: 'eng.rotem@example.com'
Subject: USACE Comments

Older text that must not be sent.
"""


def _eml(tmp_path, subject=SUBJECT, body=BODY, attachments=()):
    """attachments: (name, bytes, mime[, inline])"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = '"Kande, Mamadou" <kandem@example.com>'
    msg["To"] = "eng.rotem@example.com"
    msg["Date"] = "Sat, 17 Oct 2026 15:34:00 +0300"
    msg.set_content(body)
    for name, data, mime, *inline in attachments:
        main, sub = mime.split("/")
        extra = {"disposition": "inline", "cid": f"<{name}>"} if inline and inline[0] else {}
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name, **extra)
    path = tmp_path / "review.eml"
    path.write_bytes(bytes(msg))
    return path


def _project(root, code, name_en, docs):
    p = create_project(root, code, name_en, name_en)
    return p, [create_document(p, title_he="", title_en=title, discipline="W", subject=subj, type="shop-drawing")
               for title, subj in docs]


# ------------------------------------------------------------------------------------------- reading
def test_read_eml_headers_text_and_attachments(tmp_path):
    path = _eml(tmp_path, attachments=[("TD2MH connection.pdf", b"%PDF-1.4 fake", "application/pdf"),
                                       ("image001.png", b"\x89PNG fake", "image/png", True)])
    m = mail.read_mail(path)
    assert m.subject == SUBJECT and m.sender == "Kande, Mamadou" and m.sender_email == "kandem@example.com"
    assert m.date.startswith("2026-10-17") and len(m.date) == 16
    assert "Kindly address" in m.body and "Older text" in m.body
    assert "Older text" not in m.latest and m.latest.endswith("Papis")
    assert [a.name for a in m.files] == ["TD2MH connection.pdf"] and m.attachments[1].inline
    assert mail.review_points(m.latest) == [
        "Schematic Diagram: Replace the trench drain shown with the approved product (datasheet attached).",
        "Dimensions: Maintain the 1,200 mm vertical distance from Level ±0.00 to the top of the pipe.",
        "Original Design: Please follow the original design."]
    req = mail.request_text(m)
    assert req.startswith("Review comments received by e-mail from Kande, Mamadou <kandem@example.com> on 2026-10-17")
    assert "1.\tSchematic" in req and "Older text" not in req
    assert req.endswith("Attached to the mail: TD2MH connection.pdf")
    with pytest.raises(ValueError):
        mail.read_mail(tmp_path / "plan.pdf")


class FakeOle:
    """The few olefile calls the MSG reader makes, over a dict of streams."""
    def __init__(self, streams):
        self.streams = streams

    def exists(self, name):
        return name in self.streams

    def openstream(self, name):
        return io.BytesIO(self.streams[name])

    def listdir(self, streams=True, storages=True):
        return [name.split("/") for name in self.streams]


def _u(s):
    return s.encode("utf-16-le")


def test_msg_properties_body_date_and_attachments():
    when = datetime(2026, 10, 17, 12, 34, tzinfo=timezone.utc)
    filetime = int((when - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)
    att0, att1 = "__attach_version1.0_#00000000", "__attach_version1.0_#00000001"
    ole = FakeOle({
        "__substg1.0_0037001F": _u(SUBJECT),
        "__substg1.0_0C1A001F": _u("Kande, Mamadou"),
        "__substg1.0_0C1F001F": _u("/O=EXCHANGE/OU=GROUP/CN=RECIPIENTS/CN=KANDEM"),      # X.500, skipped
        "__substg1.0_5D01001F": _u("kandem@example.com"),
        "__substg1.0_0E04001F": _u("eng.rotem@example.com"),
        "__substg1.0_1000001F": _u(BODY),
        "__properties_version1.0": b"\0" * 32 + struct.pack("<IIQ", (0x0E06 << 16) | 0x0040, 0, filetime),
        f"{att0}/__substg1.0_3707001F": _u("SITE20117_Drawings 33.pdf"),
        f"{att0}/__substg1.0_370E001F": _u("application/pdf"),
        f"{att0}/__substg1.0_37010102": b"%PDF-1.4 fake",
        f"{att0}/__properties_version1.0": b"\0" * 8,
        f"{att1}/__substg1.0_3704001E": b"image001.png",                                  # 8-bit name
        f"{att1}/__substg1.0_370E001F": _u("image/png"),
        f"{att1}/__substg1.0_3712001F": _u("image001.png@01DD5E4C"),                      # content id: inline
        f"{att1}/__substg1.0_37010102": b"\x89PNG fake",
        f"{att1}/__properties_version1.0": b"\0" * 8 + struct.pack("<II", (0x3714 << 16) | 3, 0)
                                            + struct.pack("<I", 4) + b"\0" * 4,
    })
    m = mail.mail_from_ole(ole)
    assert m.subject == SUBJECT and m.sender == "Kande, Mamadou" and m.sender_email == "kandem@example.com"
    assert m.date == when.astimezone().strftime("%Y-%m-%d %H:%M") and m.to == "eng.rotem@example.com"
    assert [(a.name, a.inline) for a in m.attachments] == [("SITE20117_Drawings 33.pdf", False), ("image001.png", True)]
    assert m.files[0].data == b"%PDF-1.4 fake" and m.warnings == []
    assert len(mail.review_points(m.latest)) == 3

    # no plain text: the HTML body is used
    html = FakeOle({"__substg1.0_0037001F": _u("Re: TD"), "__properties_version1.0": b"\0" * 32,
                    "__substg1.0_10130102": b"<html><body><p>Hi&nbsp;Rotem,</p><p>1. Move the <b>valve</b>.</p>"
                                            b"</body></html>"})
    assert mail.mail_from_ole(html).body == "Hi\xa0Rotem,\n1. Move the valve."


def test_readers_know_mail_files(tmp_path):
    from sketch2cad.io.readers import read_any
    path = _eml(tmp_path, attachments=[("datasheet.pdf", b"%PDF", "application/pdf")])
    info = read_any(path, tmp_path / "out")
    assert info.kind == "mail" and info.pages == 1 and info.warnings == []
    assert info.text.startswith(f"Subject: {SUBJECT}") and "Attachments: datasheet.pdf" in info.text


# ------------------------------------------------------------------------------------------ matching
def test_match_document_finds_the_document_the_mail_is_about(tmp_path):
    p1, (td, camel) = _project(tmp_path, "20117", "Hatzerim", [("Trench drain connection - Building P", "TD"),
                                                                ("Connection to existing camel", "CAMEL")])
    p2, (other,) = _project(tmp_path, "30555", "Other site", [("Trench drain", "TD")])
    # subject words + the project code inside an attachment name
    m = mail.read_mail(_eml(tmp_path, attachments=[("SITE20117_Drawings 33.pdf", b"%PDF", "application/pdf")]))
    best = mail.match_document(m, [p2, p1])
    assert [x.document.code for x in best][0] == td.code and best[0].score > best[1].score
    assert any("20117" in r["en"] for r in best[0].reasons) and all(r["he"] for r in best[0].reasons)
    assert len(best) == 3                                            # every document is offered
    # the reviewer returns one of the document's own files
    camel.outputs["pdf"] = f"{camel.code}_RevA.pdf"
    camel.save()
    m2 = mail.read_mail(_eml(tmp_path, subject="Comments", body="See attached.",
                             attachments=[(f"{camel.code}_RevA.pdf", b"%PDF", "application/pdf")]))
    assert mail.match_document(m2, [p1, p2])[0].document.code == camel.code
    # the document code written in the mail
    m3 = mail.read_mail(_eml(tmp_path, subject="Re: drawing", body=f"Comments on {other.code}:\n1. Fix it."))
    assert mail.match_document(m3, [p1, p2])[0].document.code == other.code
    # nothing in common: the open project comes first
    m4 = mail.read_mail(_eml(tmp_path, subject="Hello", body="Hello."))
    assert mail.match_document(m4, [p1, p2], prefer=p2.folder)[0].project.code == "30555"
    assert mail.match_document(m4, []) == []


def test_file_mail_saves_sources_and_indexes_the_text(tmp_path):
    p, (td,) = _project(tmp_path, "20117", "Hatzerim", [("Trench drain", "TD")])
    path = _eml(tmp_path, attachments=[("TD2MH connection.pdf", b"%PDF-1.4", "application/pdf"),
                                       ("image001.png", b"\x89PNG", "image/png", True)])
    m = mail.read_mail(path)
    filed = mail.file_mail(td, m, path.read_bytes(), "review.eml")
    assert filed.mail == "sources/review.eml" and filed.attachments == ["sources/TD2MH connection.pdf"]
    assert (td.path / "sources" / "TD2MH connection.pdf").read_bytes() == b"%PDF-1.4"
    assert Document.load(td.path).sources == ["sources/review.eml", "sources/TD2MH connection.pdf"]
    assert "Trench Drain Shop Drawing Review Comments" in json.dumps(search.stored_content(td), ensure_ascii=False)
    # filing the same mail again never overwrites
    again = mail.file_mail(td, m, path.read_bytes(), "review.eml")
    assert again.mail == "sources/review_1.eml"


# ------------------------------------------------------------------------------------------- the API
def test_revise_spec_sends_the_reviewer_attachments(tmp_path):
    import pymupdf

    from sketch2cad.ai import revise
    from sketch2cad.catalog import load_catalog
    pdf = tmp_path / "SITE20117_Drawings 33.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    spec = {"units": "mm", "scale": 20,
            "elements": [{"id": "t1", "type": "trench_drain", "points": [[0, 0], [3000, 0]]}]}
    reply = {"spec": spec, "changes": [{"en": "Channel replaced", "he": "התעלה הוחלפה"}]}
    captured = {}
    rev = revise.revise_spec(spec, "Replace the trench drain with the approved product",
                             image=_white_png(tmp_path / "sheet.png"), catalog=load_catalog(),
                             attachments=[pdf, _white_png(tmp_path / "m.png")],
                             client=fake_client(json.dumps(reply), captured))
    blocks = captured["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and "no markings" in blocks[1]["text"]          # the sheet first
    assert blocks[2]["text"].endswith("in this order: SITE20117_Drawings 33.pdf, m.png")
    assert [b["type"] for b in blocks[3:5]] == ["document", "image"] and blocks[3]["title"] == pdf.name
    assert "Current spec" in blocks[5]["text"] and "approved product" in blocks[6]["text"]
    assert "reviewer's e-mail" in captured["system"] and rev.warnings == [] and rev.changes[0]["he"]


def test_api_errors_are_explained():
    import anthropic
    try:
        import httpx2 as httpx
    except ImportError:                                       # anthropic < 1.0
        import httpx
    from sketch2cad.ai.errors import explain
    from sketch2cad.i18n import t
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(cls, code, body=None):
        return cls("boom", response=httpx.Response(code, request=req, headers={"request-id": "req_1"}), body=body)

    credit = status(anthropic.BadRequestError, 400, body={"type": "error", "error": {
        "type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}})
    assert explain(credit) == ("api_no_credit", "Your credit balance is too low to access the Anthropic API. (req_1)")
    assert explain(status(anthropic.BadRequestError, 400))[0] == "api_rejected"
    assert explain(status(anthropic.AuthenticationError, 401))[0] == "api_bad_key"
    assert explain(status(anthropic.PermissionDeniedError, 403))[0] == "api_no_permission"
    assert explain(status(anthropic.RateLimitError, 429))[0] == "api_rate_limit"
    assert explain(status(anthropic.InternalServerError, 500))[0] == "api_server"
    assert explain(anthropic.APIConnectionError(request=req))[0] == "api_offline"
    assert explain(anthropic.APITimeoutError(request=req))[0] == "api_timeout"
    assert explain(ValueError("not an API error")) is None
    for key in ("api_no_credit", "api_bad_key", "api_no_permission", "api_rate_limit", "api_server", "api_offline",
                "api_timeout", "api_rejected"):
        assert t(key, "he") != key and t(key, "en") != key and "Anthropic" in t(key, "he")
