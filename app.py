#!/usr/bin/env python3
"""
PPWR Compliance Desk - local server
------------------------------------
Serves the app UI and persists all data (issuer profile, document
library, customer/supplier trackers) to a plain JSON file next to
this script, so the app behaves identically on every computer it's
copied to - no browser storage, no installation, no internet
connection required.

Run:
    python app.py
Then open the URL it prints (it also opens automatically).
"""

import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import threading
import time
import uuid
import webbrowser
from datetime import date, timedelta
from email import message_from_bytes, policy
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parseaddr, formataddr, formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
DATA_FILE = os.path.join(APP_DIR, "data.json")
ATTACHMENTS_DIR = os.path.join(APP_DIR, "attachments")
PORT = 8743

_lock = threading.Lock()

EMAIL_SETTINGS_KEY = "email-account-settings"
EMAIL_INBOX_KEY = "email-inbox"
EMAIL_ACTIVITY_KEY = "email-activity"

# Keywords that suggest a message concerns PPWR / packaging compliance at all.
PPWR_KEYWORDS = [
    "ppwr", "packaging waste regulation", "declaration of conformity",
    "doc no", "conformity declaration", "packaging compliance",
    "recyclab", "recycled content", "epr registration",
    "supplier declaration", "compliance certificate", "annex viii",
    "packaging regulation", "eu 2025/40",
]

# Phrasing that suggests a simple, template-answerable ask (as opposed to a
# complex question that genuinely needs a human to think about the reply).
ROUTINE_ASK_PATTERNS = [
    r"\bcould you (please )?send\b", r"\bcan you (please )?send\b",
    r"\bplease (send|provide|share)\b", r"\bcould you (please )?provide\b",
    r"\bdo you have\b.*\b(doc|declaration|certificate)\b",
    r"\bneed(?:ing)? (a|the) (copy of )?(the )?(doc|declaration|certificate)\b",
    r"\brequest(?:ing)? (a|the) (copy of )?(the )?(doc|declaration|certificate)\b",
]

# Fallback pattern for pulling a plausible SKU/reference straight out of the
# email text when there's no saved document to match it against — e.g. "SKU
# TEST-001" or "SKU: ABC-123". This is a much weaker signal than an exact
# library match (it's just "the sender typed something after the word SKU"),
# so it's only ever used to fill in the tracker's SKU field, never to
# trigger a Routine auto-reply.
SKU_HINT_PATTERN = re.compile(r"\bsku\b[\s:#\-]*([A-Za-z0-9][A-Za-z0-9\-_/]{1,24})", re.IGNORECASE)


def extract_sku_hint(text):
    m = SKU_HINT_PATTERN.search(text)
    return m.group(1).strip(".,;:") if m else None


# ---------------------------------------------------------------------------
# Due-date extraction: look for an explicit deadline the sender mentioned,
# in whichever common format they happened to use, rather than always
# falling back to a flat +7-day guess.
# ---------------------------------------------------------------------------

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Only look for a date within a short window after one of these deadline
# cue phrases, rather than any date-shaped text anywhere in the email —
# otherwise an unrelated date (an old document's issue date, a meeting time)
# could get mistaken for a requested deadline.
DUE_DATE_CUE_PATTERN = re.compile(
    r"\b(?:by|before|until|no later than|deadline(?:\s*(?:is|of|:))?|"
    r"due(?:\s*(?:date|by|on))?|required by|need(?:ed)?\s*(?:it\s*)?by)\b",
    re.IGNORECASE,
)

_DATE_FRAGMENT_PATTERNS = [
    # "12th of September, 2026" / "12 September 2026"
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", re.IGNORECASE),
    # "September 12, 2026" / "Sep 12th 2026"
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.IGNORECASE),
    # ISO "2026-09-12"
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
    # Numeric "12.09.2026" or "12/09/2026" — treated as day.month.year (the
    # convention used across the EU), with a US month/day fallback if that
    # reading isn't a valid date.
    re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b"),
]


def _safe_date(y, mo, d):
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _parse_date_fragment(fragment):
    m = _DATE_FRAGMENT_PATTERNS[0].search(fragment)
    if m:
        day, mon_name, year = m.groups()
        mon = MONTH_NAMES.get(mon_name.lower())
        if mon:
            d = _safe_date(int(year), mon, int(day))
            if d:
                return d

    m = _DATE_FRAGMENT_PATTERNS[1].search(fragment)
    if m:
        mon_name, day, year = m.groups()
        mon = MONTH_NAMES.get(mon_name.lower())
        if mon:
            d = _safe_date(int(year), mon, int(day))
            if d:
                return d

    m = _DATE_FRAGMENT_PATTERNS[2].search(fragment)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        d = _safe_date(y, mo, d)
        if d:
            return d

    m = _DATE_FRAGMENT_PATTERNS[3].search(fragment)
    if m:
        a, b, y = (int(x) for x in m.groups())
        d = _safe_date(y, b, a)  # day.month.year
        if d:
            return d
        d = _safe_date(y, a, b)  # fall back to month/day/year
        if d:
            return d

    return None


def extract_due_date_hint(text, today=None):
    """Looks for an explicit deadline near a cue phrase like "by", "no
    later than", "deadline". Returns an ISO date string, or None if nothing
    usable was found. Rejects dates in the past or more than ~2 years out,
    since those are almost certainly a misparse rather than a real
    deadline."""
    today = today or date.today()
    for m in DUE_DATE_CUE_PATTERN.finditer(text):
        window = text[m.end():m.end() + 40]
        found = _parse_date_fragment(window)
        if found and today <= found <= today + timedelta(days=730):
            return found.isoformat()
    return None


# ---------------------------------------------------------------------------
# Supplier-side matching: recognising when an incoming email is from a
# supplier already on the Supplier Documentation Tracker, so the
# correspondence can be logged there automatically instead of being treated
# as a customer inquiry.
# ---------------------------------------------------------------------------

DOC_TYPE_HINTS = [
    (re.compile(r"\bpfas\b", re.IGNORECASE), "PFAS statement"),
    (re.compile(r"\breach\b", re.IGNORECASE), "REACH declaration"),
    (re.compile(r"\brecycled content\b", re.IGNORECASE), "Recycled content certificate"),
    (re.compile(r"\bmaterial declaration\b", re.IGNORECASE), "Material declaration"),
    (re.compile(r"\btest report\b", re.IGNORECASE), "Test report"),
    (re.compile(r"\bdeclaration of conformity\b", re.IGNORECASE), "Declaration of conformity"),
    (re.compile(r"\bsafety data sheet\b|\bsds\b", re.IGNORECASE), "Safety data sheet"),
    (re.compile(r"\brecyclab", re.IGNORECASE), "Recyclability statement"),
]


def extract_doc_type_hint(text):
    """Returns every document type recognised in the text (not just the
    first), comma-joined, so a supplier mentioning several documents at
    once — "attached are our REACH declaration and PFAS statement" — gets
    all of them captured rather than only whichever pattern happens to be
    checked first."""
    found = []
    for pat, label in DOC_TYPE_HINTS:
        if pat.search(text) and label not in found:
            found.append(label)
    return ", ".join(found) if found else None


def match_supplier(parsed, suppliers, haystack):
    """Recognises a known supplier by name (matched in the email text or
    the sender's display name) or, failing that, by the domain of a
    contact email saved on their tracker entry — whichever matches
    first, checked in that order."""
    for s in suppliers:
        name = (s.get("supplier") or "").strip()
        if not name or len(name) < 3:
            continue
        if _contains_whole(name.lower(), haystack) or name.lower() in (parsed.get("fromName") or "").lower():
            return s

    from_addr = (parsed.get("fromAddr") or "").lower()
    from_domain = from_addr.split("@")[-1] if "@" in from_addr else ""
    if from_domain:
        for s in suppliers:
            contact = (s.get("contactEmail") or "").strip().lower()
            if "@" in contact and contact.split("@")[-1] == from_domain:
                return s
    return None


def _make_supplier_entry_from_email(parsed, matched_supplier, entry):
    """Builds a Supplier Documentation Tracker record from an incoming
    email that matched a known supplier, so correspondence logged by email
    shows up next to entries added by hand."""
    raw_text = parsed["subject"] + "\n" + parsed["bodyText"]

    found_doc_type = extract_doc_type_hint(raw_text)
    doc_type = found_doc_type or matched_supplier.get("docType") or ""

    found_sku = extract_sku_hint(raw_text)
    sku = found_sku or matched_supplier.get("sku") or ""

    followup_hint = extract_due_date_hint(raw_text)
    today = _today_iso()
    followup_date = followup_hint or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 7 * 86400))

    notes = f'Auto-logged from incoming email — "{parsed["subject"]}" from {parsed.get("fromAddr") or "unknown sender"}.'
    notes += " (Completeness not verified automatically — please review and update.)"
    if not found_doc_type and matched_supplier.get("docType"):
        notes += " Document type carried over from the existing supplier record — please confirm it still applies."
    if followup_hint:
        notes += " (A date mentioned in the email was used as the follow-up date — please double-check it.)"
    if entry.get("attachments"):
        names = ", ".join(a["filename"] for a in entry["attachments"])
        notes += f" Attached: {names}."

    return {
        "id": "sup_" + uuid.uuid4().hex[:10],
        "supplier": matched_supplier.get("supplier") or parsed.get("fromName") or parsed.get("fromAddr") or "Unknown supplier",
        "docType": doc_type,
        "sku": sku,
        "receivedDate": today,
        "completeness": "Incomplete",
        "followupDate": followup_date,
        "missing": "",
        "notes": notes,
        "contactEmail": matched_supplier.get("contactEmail", ""),
        "sourceEmailId": entry["id"],
        "attachments": entry.get("attachments", []),
    }


def _load_data():
    if not os.path.exists(DATA_FILE):
        return {"personal": {}, "shared": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("personal", {})
            data.setdefault("shared", {})
            return data
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable file: back it up and start fresh
        # rather than crashing the app or silently losing writes.
        try:
            os.replace(DATA_FILE, DATA_FILE + ".corrupt-backup")
        except OSError:
            pass
        return {"personal": {}, "shared": {}}


def _save_data(data):
    tmp_path = DATA_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, DATA_FILE)  # atomic on POSIX and Windows


# ---------------------------------------------------------------------------
# Small helpers for reading/writing the same "personal" JSON-string buckets
# that the frontend's storageGet/storageSet already use, so values written by
# either side stay in sync automatically.
# ---------------------------------------------------------------------------

def _bucket_get(data, key, default):
    raw = data["personal"].get(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _bucket_set(data, key, value):
    data["personal"][key] = json.dumps(value, ensure_ascii=False)


def _decode_mime_words(s):
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _extract_body_text(msg):
    """Best-effort plain-text body extraction from a parsed email.message.Message."""
    if msg.is_multipart():
        # Prefer a text/plain part; fall back to a stripped text/html part.
        plain, html = None, None
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            if ctype == "text/plain" and plain is None:
                plain = part
            elif ctype == "text/html" and html is None:
                html = part
        target = plain or html
        if target is None:
            return ""
        try:
            raw = target.get_content()
        except Exception:
            payload = target.get_payload(decode=True) or b""
            raw = payload.decode(target.get_content_charset() or "utf-8", errors="replace")
        if target is html:
            raw = re.sub(r"<[^>]+>", " ", raw)
        return raw
    else:
        try:
            raw = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True) or b""
            raw = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            raw = re.sub(r"<[^>]+>", " ", raw)
        return raw


def _today_iso():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _make_customer_request_from_email(parsed, verdict, entry, library, customers):
    """Builds a Customer Requests tracker record from an incoming email that
    was classified as PPWR-relevant, so requests logged by email show up
    next to ones added by hand."""
    matched_doc = next((d for d in library if d.get("id") == verdict["matchedDocId"]), None)
    matched_customer = next((c for c in customers if c.get("id") == verdict["matchedCustomerId"]), None)

    customer_name = (matched_customer.get("customer") if matched_customer else None) \
        or parsed.get("fromName") or parsed.get("fromAddr") or "Unknown sender"

    sku = ""
    sku_is_hint = False
    if matched_doc:
        sku = matched_doc.get("sku") or matched_doc.get("tradeDesignation") or ""
    elif verdict.get("skuHint"):
        sku = verdict["skuHint"]
        sku_is_hint = True

    was_auto_replied = entry["status"] == "auto_replied"
    today = _today_iso()
    # Prefer an explicit deadline the sender actually wrote ("by 12
    # September 2026", "no later than 20.10.2026", etc.); fall back to a
    # one-week reminder if nothing usable was found. Auto-replied requests
    # don't need a due date chasing them.
    due_date_is_extracted = False
    if was_auto_replied:
        due_date = ""
    elif verdict.get("dueDateHint"):
        due_date = verdict["dueDateHint"]
        due_date_is_extracted = True
    else:
        due_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 7 * 86400))

    notes = f'Auto-logged from incoming email — "{parsed["subject"]}" from {parsed.get("fromAddr") or "unknown sender"}.'
    if sku_is_hint:
        notes += " (SKU read from the email text — not yet matched to a saved document; please verify.)"
    if due_date_is_extracted:
        notes += " (Due date detected from the email text — please double-check it.)"
    if entry.get("attachments"):
        names = ", ".join(a["filename"] for a in entry["attachments"])
        notes += f" Attached: {names}."

    return {
        "id": "cust_" + uuid.uuid4().hex[:10],
        "customer": customer_name,
        "sku": sku,
        "assigned": "",
        "requestedDate": today,
        "dueDate": due_date,
        "status": "Sent" if was_auto_replied else "Open",
        "sentDate": today if was_auto_replied else "",
        "notes": notes,
        "sourceEmailId": entry["id"],
        "attachments": entry.get("attachments", []),
    }



def _connect_imap(settings, timeout=20):
    host = settings["imapHost"]
    port = int(settings.get("imapPort") or 993)
    security = settings.get("security", "ssl")
    if security == "ssl":
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        conn = imaplib.IMAP4(host, port, timeout=timeout)
        if security == "starttls":
            conn.starttls(ssl.create_default_context())
    conn.login(settings["username"], settings["password"])
    return conn


def _connect_smtp(settings, timeout=20):
    host = settings["smtpHost"]
    port = int(settings.get("smtpPort") or 587)
    security = settings.get("smtpSecurity", "starttls")
    if security == "ssl":
        conn = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        conn = smtplib.SMTP(host, port, timeout=timeout)
        if security == "starttls":
            conn.starttls(context=ssl.create_default_context())
    conn.login(settings["username"], settings["password"])
    return conn


def _extract_attachments(msg, max_total_bytes=15 * 1024 * 1024):
    """Pulls real file attachments (not inline body parts) out of a parsed
    email, capped at a combined size so one huge attachment can't fill the
    disk. Returns a list of {filename, contentType, data} with data as raw
    bytes — nothing is written to disk here, that happens once we know
    which inbox entry these belong to."""
    if not msg.is_multipart():
        return []
    found = []
    total = 0
    for part in msg.walk():
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition != "attachment" and not filename:
            continue
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decode_mime_words(filename) if filename else "attachment"
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        if total + len(payload) > max_total_bytes:
            continue  # skip anything past the combined size cap
        total += len(payload)
        found.append({
            "filename": filename,
            "contentType": part.get_content_type() or "application/octet-stream",
            "data": payload,
        })
    return found


def _sanitize_filename(name):
    name = os.path.basename(name or "attachment")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "attachment"


def _save_attachments(entry_id, raw_attachments):
    """Writes each attachment's bytes to disk under attachments/<entry_id>/
    and returns JSON-safe metadata (no raw bytes) to store on the inbox
    entry and any tracker row created from it."""
    if not raw_attachments:
        return []
    entry_dir = os.path.join(ATTACHMENTS_DIR, entry_id)
    os.makedirs(entry_dir, exist_ok=True)
    saved = []
    for i, att in enumerate(raw_attachments):
        safe_name = _sanitize_filename(att["filename"])
        saved_as = f"{i:02d}_{safe_name}"
        try:
            with open(os.path.join(entry_dir, saved_as), "wb") as f:
                f.write(att["data"])
        except OSError:
            continue
        saved.append({
            "filename": att["filename"],
            "savedAs": saved_as,
            "contentType": att["contentType"],
            "size": len(att["data"]),
        })
    return saved


def fetch_new_emails(settings, known_message_ids, lookback_days=14, limit=60):
    """Connects over IMAP and returns parsed dicts for messages not already
    present in known_message_ids (matched by RFC Message-ID)."""
    conn = _connect_imap(settings)
    results = []
    try:
        folder = settings.get("imapFolder") or "INBOX"
        conn.select(folder, readonly=True)
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - lookback_days * 86400))
        status, data = conn.search(None, f'(SINCE "{since}")')
        if status != "OK":
            raise RuntimeError("IMAP search failed")
        ids = data[0].split()
        ids = ids[-limit:]  # most recent N
        for num in ids:
            status, msg_data = conn.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = message_from_bytes(raw, policy=policy.default)
            message_id = (msg.get("Message-ID") or "").strip()
            if message_id and message_id in known_message_ids:
                continue
            from_name, from_addr = parseaddr(_decode_mime_words(msg.get("From", "")))
            subject = _decode_mime_words(msg.get("Subject", "(no subject)"))
            body = _extract_body_text(msg).strip()
            attachments = _extract_attachments(msg)
            results.append({
                "messageId": message_id or make_msgid(),
                "fromName": from_name,
                "fromAddr": from_addr,
                "subject": subject or "(no subject)",
                "date": msg.get("Date", ""),
                "bodyText": body[:8000],
                "references": msg.get("References", ""),
                "attachments": attachments,
            })
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()
    return results


# Automated systems (newsletters, security alerts, social-network digests,
# etc.) never need a PPWR reply logged against them, no matter what
# incidental words appear in the subject/body. Recognised by sender address
# patterns rather than a domain allowlist, since these prefixes are used
# near-universally for non-human senders.
AUTOMATED_SENDER_PATTERN = re.compile(
    r"(no-?reply|do-?not-?reply|notifications?|newsletter|alerts?|"
    r"mailer-daemon|postmaster|bounce|automated)@",
    re.IGNORECASE,
)


def _contains_whole(value, haystack):
    """Substring match with word boundaries, so a short/generic saved SKU
    or trade designation (e.g. "New", "Eco") can't false-positive by
    matching inside an unrelated longer word."""
    if not value:
        return False
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", haystack) is not None


def classify_email(parsed, library, customers):
    """Rule-based classification (no AI/network calls beyond this local
    matching). Returns a dict describing relevance, best match, and a draft."""
    haystack = (parsed["subject"] + " \n " + parsed["bodyText"]).lower()

    if AUTOMATED_SENDER_PATTERN.search(parsed.get("fromAddr") or ""):
        return {
            "classification": "not_relevant",
            "needsResponse": False,
            "matchedDocId": None,
            "matchedCustomerId": None,
            "draftReply": "",
            "skuHint": None,
            "dueDateHint": None,
        }

    is_ppwr_relevant = any(kw in haystack for kw in PPWR_KEYWORDS)

    # Try to find a library document whose SKU or trade designation is
    # literally mentioned in the message.
    matched_doc = None
    for doc in library:
        for field in ("sku", "tradeDesignation"):
            val = (doc.get(field) or "").strip()
            if val and len(val) >= 3 and _contains_whole(val.lower(), haystack):
                matched_doc = doc
                break
        if matched_doc:
            break

    # Try to find a tracked customer name mentioned in the message.
    matched_customer = None
    for cust in customers:
        name = (cust.get("customer") or "").strip()
        if name and len(name) >= 3 and _contains_whole(name.lower(), haystack):
            matched_customer = cust
            break
        if not matched_customer and name and name.lower() in parsed["fromName"].lower():
            matched_customer = cust

    if not is_ppwr_relevant and not matched_doc:
        return {
            "classification": "not_relevant",
            "needsResponse": False,
            "matchedDocId": None,
            "matchedCustomerId": None,
            "draftReply": "",
            "skuHint": None,
            "dueDateHint": None,
        }

    sku_hint = None if matched_doc else extract_sku_hint(parsed["subject"] + " " + parsed["bodyText"])
    due_date_hint = extract_due_date_hint(parsed["subject"] + "\n" + parsed["bodyText"])
    looks_routine = any(re.search(pat, haystack) for pat in ROUTINE_ASK_PATTERNS)
    sender_greeting = parsed["fromName"].split()[0] if parsed["fromName"] else "there"

    if matched_doc and looks_routine:
        draft = (
            f"Dear {sender_greeting},\n\n"
            f"Thank you for your message regarding {matched_doc.get('tradeDesignation') or matched_doc.get('sku') or 'this product'}.\n\n"
            f"Please find the key details of our current EU PPWR Declaration of Conformity below:\n\n"
            f"- Document No.: {matched_doc.get('documentNo','—')}\n"
            f"- Revision: {matched_doc.get('revision','—')}\n"
            f"- Issue date: {matched_doc.get('issueDate','—')}\n"
            f"- Trade designation / SKU: {matched_doc.get('tradeDesignation','—')} / {matched_doc.get('sku','—')}\n"
            f"- Recyclability grade: {matched_doc.get('recyclabilityGrade','—')}\n\n"
            f"If you require the full signed PDF certificate, just let us know and we'll send it across separately.\n\n"
            f"Best regards"
        )
        return {
            "classification": "routine",
            "needsResponse": True,
            "matchedDocId": matched_doc.get("id"),
            "matchedCustomerId": matched_customer.get("id") if matched_customer else None,
            "draftReply": draft,
            "skuHint": None,
            "dueDateHint": due_date_hint,
        }

    ref = matched_doc.get("tradeDesignation") or matched_doc.get("sku") if matched_doc else None
    draft = (
        f"Dear {sender_greeting},\n\n"
        f"Thank you for your message regarding PPWR / packaging compliance"
        + (f" for {ref}" if ref else "") + ".\n\n"
        f"We're reviewing your request and will follow up shortly with the relevant documentation.\n\n"
        f"Best regards"
    )
    return {
        "classification": "needs_review",
        "needsResponse": True,
        "matchedDocId": matched_doc.get("id") if matched_doc else None,
        "matchedCustomerId": matched_customer.get("id") if matched_customer else None,
        "draftReply": draft,
        "skuHint": sku_hint,
        "dueDateHint": due_date_hint,
    }


def send_reply_email(settings, to_addr, subject, body_text, in_reply_to=None, references=None):
    from_addr = settings.get("username")
    from_name = settings.get("fromName") or ""
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    reply_to = settings.get("replyTo")
    if reply_to:
        msg["Reply-To"] = reply_to

    conn = _connect_smtp(settings)
    try:
        conn.send_message(msg)
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def send_plain_email(settings, to_addr, subject, body_text):
    """Sends a standalone email — not a reply to any existing thread, so no
    'Re:' prefix or In-Reply-To headers. Used for manually-triggered
    outbound notifications like a supplier follow-up."""
    from_addr = settings.get("username")
    from_name = settings.get("fromName") or ""
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    reply_to = settings.get("replyTo")
    if reply_to:
        msg["Reply-To"] = reply_to

    conn = _connect_smtp(settings)
    try:
        conn.send_message(msg)
    finally:
        try:
            conn.quit()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "PPWRComplianceDesk/1.0"

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "":
            self._send_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            return

        if path == "/api/ping":
            self._send_json(200, {"ok": True})
            return

        if path == "/api/storage/get":
            key = qs.get("key", [None])[0]
            shared = qs.get("shared", ["false"])[0] == "true"
            if not key:
                self._send_json(400, {"error": "missing key"})
                return
            with _lock:
                data = _load_data()
            bucket = "shared" if shared else "personal"
            value = data[bucket].get(key)
            if value is None:
                self._send_json(404, {"error": "not found"})
                return
            self._send_json(200, {"key": key, "value": value, "shared": shared})
            return

        if path == "/api/storage/list":
            prefix = qs.get("prefix", [""])[0]
            shared = qs.get("shared", ["false"])[0] == "true"
            with _lock:
                data = _load_data()
            bucket = "shared" if shared else "personal"
            keys = [k for k in data[bucket].keys() if k.startswith(prefix)]
            self._send_json(200, {"keys": keys, "prefix": prefix, "shared": shared})
            return

        if path == "/api/backup":
            with _lock:
                data = _load_data()
            # Flatten personal store to {key: parsedValue} for a
            # human-readable, portable backup file.
            flat = {}
            for k, v in data["personal"].items():
                try:
                    flat[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    flat[k] = v
            # Never put mailbox credentials into a portable backup file -
            # those get emailed to colleagues, copied to other machines,
            # etc. Strip the password so it can't leak that way.
            if isinstance(flat.get(EMAIL_SETTINGS_KEY), dict):
                flat[EMAIL_SETTINGS_KEY] = {k: v for k, v in flat[EMAIL_SETTINGS_KEY].items() if k != "password"}
            self._send_json(200, flat)
            return

        if path == "/api/email/settings":
            with _lock:
                data = _load_data()
            settings = _bucket_get(data, EMAIL_SETTINGS_KEY, None)
            if settings:
                safe = dict(settings)
                safe["password"] = "" if not safe.get("password") else "__saved__"
            else:
                safe = None
            self._send_json(200, {"settings": safe})
            return

        if path == "/api/email/attachment":
            email_id = qs.get("emailId", [None])[0]
            file_key = qs.get("file", [None])[0]
            if not email_id or not file_key:
                self._send_json(400, {"error": "missing emailId or file"})
                return
            with _lock:
                data = _load_data()
                inbox = _bucket_get(data, EMAIL_INBOX_KEY, [])
            entry = next((e for e in inbox if e.get("id") == email_id), None)
            att = next((a for a in (entry or {}).get("attachments", []) if a.get("savedAs") == file_key), None)
            if not att:
                self.send_error(404, "Not found")
                return
            file_path = os.path.join(ATTACHMENTS_DIR, email_id, file_key)
            try:
                with open(file_path, "rb") as f:
                    body_bytes = f.read()
            except OSError:
                self.send_error(404, "Not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", att.get("contentType") or "application/octet-stream")
            self.send_header("Content-Length", str(len(body_bytes)))
            safe_name = (att.get("filename") or "file").replace('"', "")
            self.send_header("Content-Disposition", f'inline; filename="{safe_name}"')
            self.end_headers()
            self.wfile.write(body_bytes)
            return

        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if path == "/api/storage/set":
            key = body.get("key")
            value = body.get("value")
            shared = bool(body.get("shared", False))
            if not key or value is None:
                self._send_json(400, {"error": "missing key or value"})
                return
            bucket = "shared" if shared else "personal"
            with _lock:
                data = _load_data()
                data[bucket][key] = value
                _save_data(data)
            self._send_json(200, {"key": key, "value": value, "shared": shared})
            return

        if path == "/api/storage/delete":
            key = body.get("key")
            shared = bool(body.get("shared", False))
            if not key:
                self._send_json(400, {"error": "missing key"})
                return
            bucket = "shared" if shared else "personal"
            with _lock:
                data = _load_data()
                existed = data[bucket].pop(key, None) is not None
                _save_data(data)
            self._send_json(200, {"key": key, "deleted": existed, "shared": shared})
            return

        if path == "/api/restore":
            # body is {key: parsedValue, ...} as produced by /api/backup
            with _lock:
                data = _load_data()
                for k, v in body.items():
                    data["personal"][k] = json.dumps(v)
                _save_data(data)
            self._send_json(200, {"restored": list(body.keys())})
            return

        if path == "/api/email/settings":
            with _lock:
                data = _load_data()
                existing = _bucket_get(data, EMAIL_SETTINGS_KEY, {}) or {}
                # If the client sent the "__saved__" sentinel (password field
                # left untouched in the UI), keep the previously stored one.
                if body.get("password") == "__saved__":
                    body["password"] = existing.get("password", "")
                _bucket_set(data, EMAIL_SETTINGS_KEY, body)
                _save_data(data)
            self._send_json(200, {"ok": True})
            return

        if path == "/api/email/test":
            settings = body
            with _lock:
                data = _load_data()
                existing = _bucket_get(data, EMAIL_SETTINGS_KEY, {}) or {}
            if settings.get("password") == "__saved__":
                settings["password"] = existing.get("password", "")
            imap_result = {"ok": False, "message": ""}
            smtp_result = {"ok": False, "message": ""}
            try:
                conn = _connect_imap(settings)
                conn.select(settings.get("imapFolder") or "INBOX", readonly=True)
                conn.close()
                conn.logout()
                imap_result = {"ok": True, "message": "IMAP login and folder access OK."}
            except Exception as e:
                imap_result = {"ok": False, "message": f"IMAP failed: {e}"}
            try:
                conn = _connect_smtp(settings)
                conn.quit()
                smtp_result = {"ok": True, "message": "SMTP login OK."}
            except Exception as e:
                smtp_result = {"ok": False, "message": f"SMTP failed: {e}"}
            self._send_json(200, {"imap": imap_result, "smtp": smtp_result})
            return

        if path == "/api/email/check":
            with _lock:
                data = _load_data()
                settings = _bucket_get(data, EMAIL_SETTINGS_KEY, None)
                inbox = _bucket_get(data, EMAIL_INBOX_KEY, [])
                activity = _bucket_get(data, EMAIL_ACTIVITY_KEY, [])
                library = _bucket_get(data, "doc-library", [])
                customers = _bucket_get(data, "customer-requests", [])
                suppliers = _bucket_get(data, "supplier-tracker", [])

            if not settings or not settings.get("imapHost") or not settings.get("username"):
                self._send_json(400, {"error": "Email account is not configured yet."})
                return

            known_ids = {e.get("messageId") for e in inbox if e.get("messageId")}
            try:
                fetched = fetch_new_emails(settings, known_ids,
                                            lookback_days=int(settings.get("lookbackDays") or 14))
            except Exception as e:
                self._send_json(502, {"error": f"Could not check email: {e}"})
                return

            auto_replied, needs_review, not_relevant, new_customer_requests, new_supplier_entries = 0, 0, 0, 0, 0
            for parsed in fetched:
                haystack = (parsed["subject"] + " \n " + parsed["bodyText"]).lower()
                is_automated = bool(AUTOMATED_SENDER_PATTERN.search(parsed.get("fromAddr") or ""))
                matched_supplier = None if is_automated else match_supplier(parsed, suppliers, haystack)

                entry = {
                    "id": "eml_" + uuid.uuid4().hex[:10],
                    "messageId": parsed["messageId"],
                    "fromName": parsed["fromName"],
                    "fromAddr": parsed["fromAddr"],
                    "subject": parsed["subject"],
                    "date": parsed["date"],
                    "bodyText": parsed["bodyText"],
                    "references": parsed["references"],
                    "fetchedAt": time.time(),
                    "customerRequestId": None,
                    "supplierRequestId": None,
                }

                if matched_supplier:
                    # Supplier correspondence: logged straight to the
                    # Supplier Tracker, no reply drafted — this pipeline is
                    # about recording what came in, not answering it.
                    entry.update({
                        "classification": "supplier_logged",
                        "needsResponse": False,
                        "matchedDocId": None,
                        "matchedCustomerId": None,
                        "matchedSupplierId": matched_supplier.get("id"),
                        "draftReply": "",
                        "status": "logged",
                        "repliedAt": None,
                        "repliedBody": None,
                        "attachments": _save_attachments(entry["id"], parsed.get("attachments")),
                    })
                    sup_entry = _make_supplier_entry_from_email(parsed, matched_supplier, entry)
                    suppliers.append(sup_entry)
                    entry["supplierRequestId"] = sup_entry["id"]
                    new_supplier_entries += 1
                    inbox.insert(0, entry)
                    continue

                verdict = classify_email(parsed, library, customers)
                entry.update({
                    "classification": verdict["classification"],
                    "needsResponse": verdict["needsResponse"],
                    "matchedDocId": verdict["matchedDocId"],
                    "matchedCustomerId": verdict["matchedCustomerId"],
                    "matchedSupplierId": None,
                    "draftReply": verdict["draftReply"],
                    "status": "new",
                    "repliedAt": None,
                    "repliedBody": None,
                    "attachments": _save_attachments(entry["id"], parsed.get("attachments"))
                                   if verdict["classification"] != "not_relevant" else [],
                })

                if verdict["classification"] == "routine" and settings.get("autoSendEnabled") and parsed["fromAddr"]:
                    try:
                        send_reply_email(settings, parsed["fromAddr"], parsed["subject"],
                                          verdict["draftReply"], in_reply_to=parsed["messageId"],
                                          references=parsed["references"])
                        entry["status"] = "auto_replied"
                        entry["repliedAt"] = time.time()
                        entry["repliedBody"] = verdict["draftReply"]
                        activity.insert(0, {
                            "id": "act_" + uuid.uuid4().hex[:10],
                            "at": time.time(),
                            "type": "auto_reply",
                            "subject": parsed["subject"],
                            "to": parsed["fromAddr"],
                        })
                        auto_replied += 1
                    except Exception as e:
                        entry["status"] = "auto_send_failed"
                        entry["repliedBody"] = f"(Auto-send failed: {e})"
                        needs_review += 1
                elif verdict["classification"] == "not_relevant":
                    not_relevant += 1
                else:
                    needs_review += 1

                # Log a Customer Requests entry for anything PPWR-relevant,
                # so requests that come in by email show up in the same
                # tracker as ones added by hand.
                if verdict["classification"] in ("routine", "needs_review"):
                    cust_entry = _make_customer_request_from_email(parsed, verdict, entry, library, customers)
                    customers.append(cust_entry)
                    entry["customerRequestId"] = cust_entry["id"]
                    new_customer_requests += 1

                inbox.insert(0, entry)

            activity.insert(0, {
                "id": "act_" + uuid.uuid4().hex[:10],
                "at": time.time(),
                "type": "check",
                "subject": f"Checked inbox — {len(fetched)} new message(s)",
                "to": "",
            })
            inbox = inbox[:500]
            activity = activity[:200]

            with _lock:
                data = _load_data()
                _bucket_set(data, EMAIL_INBOX_KEY, inbox)
                _bucket_set(data, EMAIL_ACTIVITY_KEY, activity)
                _bucket_set(data, "customer-requests", customers)
                _bucket_set(data, "supplier-tracker", suppliers)
                settings["lastCheckedAt"] = time.time()
                _bucket_set(data, EMAIL_SETTINGS_KEY, settings)
                _save_data(data)

            self._send_json(200, {
                "fetched": len(fetched),
                "autoReplied": auto_replied,
                "needsReview": needs_review,
                "notRelevant": not_relevant,
                "newCustomerRequests": new_customer_requests,
                "newSupplierEntries": new_supplier_entries,
            })
            return

        if path == "/api/email/reclassify":
            email_id = body.get("emailId")
            if not email_id:
                self._send_json(400, {"error": "missing emailId"})
                return
            with _lock:
                data = _load_data()
                inbox = _bucket_get(data, EMAIL_INBOX_KEY, [])
                customers = _bucket_get(data, "customer-requests", [])
                library = _bucket_get(data, "doc-library", [])

            entry = next((e for e in inbox if e.get("id") == email_id), None)
            if not entry:
                self._send_json(404, {"error": "email not found"})
                return
            if entry["status"] not in ("new", "auto_send_failed"):
                self._send_json(400, {"error": "This email has already been handled (replied to or logged), so its classification is locked in."})
                return

            parsed = {
                "fromName": entry["fromName"], "fromAddr": entry["fromAddr"],
                "subject": entry["subject"], "bodyText": entry["bodyText"],
            }
            verdict = classify_email(parsed, library, customers)
            entry["classification"] = verdict["classification"]
            entry["needsResponse"] = verdict["needsResponse"]
            entry["matchedDocId"] = verdict["matchedDocId"]
            entry["matchedCustomerId"] = verdict["matchedCustomerId"]
            entry["draftReply"] = verdict["draftReply"]

            # If this email already has a linked Customer Requests entry,
            # refresh its SKU/notes to reflect the new match instead of
            # creating a second, duplicate entry.
            linked = next((c for c in customers if c.get("id") == entry.get("customerRequestId")), None)
            if linked and verdict["classification"] in ("routine", "needs_review"):
                refreshed = _make_customer_request_from_email(parsed, verdict, entry, library, customers)
                linked["sku"] = refreshed["sku"]
                linked["notes"] = refreshed["notes"] + " (Re-checked after the document library was updated.)"
            elif not linked and verdict["classification"] in ("routine", "needs_review"):
                new_cust = _make_customer_request_from_email(parsed, verdict, entry, library, customers)
                customers.append(new_cust)
                entry["customerRequestId"] = new_cust["id"]

            with _lock:
                data = _load_data()
                _bucket_set(data, EMAIL_INBOX_KEY, inbox)
                _bucket_set(data, "customer-requests", customers)
                _save_data(data)
            self._send_json(200, {"ok": True, "entry": entry})
            return

        if path == "/api/email/send":
            email_id = body.get("emailId")
            reply_body = body.get("body", "")
            if not email_id or not reply_body:
                self._send_json(400, {"error": "missing emailId or body"})
                return
            with _lock:
                data = _load_data()
                settings = _bucket_get(data, EMAIL_SETTINGS_KEY, None)
                inbox = _bucket_get(data, EMAIL_INBOX_KEY, [])
                activity = _bucket_get(data, EMAIL_ACTIVITY_KEY, [])
                customers = _bucket_get(data, "customer-requests", [])
            if not settings:
                self._send_json(400, {"error": "Email account is not configured yet."})
                return
            entry = next((e for e in inbox if e.get("id") == email_id), None)
            if not entry:
                self._send_json(404, {"error": "email not found"})
                return
            try:
                send_reply_email(settings, entry["fromAddr"], entry["subject"], reply_body,
                                  in_reply_to=entry.get("messageId"), references=entry.get("references"))
            except Exception as e:
                self._send_json(502, {"error": f"Send failed: {e}"})
                return
            entry["status"] = "replied"
            entry["repliedAt"] = time.time()
            entry["repliedBody"] = reply_body
            activity.insert(0, {
                "id": "act_" + uuid.uuid4().hex[:10],
                "at": time.time(),
                "type": "manual_reply",
                "subject": entry["subject"],
                "to": entry["fromAddr"],
            })
            # Keep the linked Customer Requests entry (if this email created
            # one) in sync now that it's actually been answered.
            linked = next((c for c in customers if c.get("id") == entry.get("customerRequestId")), None)
            if linked:
                linked["status"] = "Sent"
                linked["sentDate"] = _today_iso()
            with _lock:
                data = _load_data()
                _bucket_set(data, EMAIL_INBOX_KEY, inbox)
                _bucket_set(data, EMAIL_ACTIVITY_KEY, activity[:200])
                _bucket_set(data, "customer-requests", customers)
                _save_data(data)
            self._send_json(200, {"ok": True, "entry": entry})
            return

        if path == "/api/email/notify-supplier":
            supplier_id = body.get("supplierId")
            subject = (body.get("subject") or "").strip()
            notify_body = (body.get("body") or "").strip()
            if not supplier_id or not subject or not notify_body:
                self._send_json(400, {"error": "missing supplierId, subject or body"})
                return
            with _lock:
                data = _load_data()
                settings = _bucket_get(data, EMAIL_SETTINGS_KEY, None)
                suppliers = _bucket_get(data, "supplier-tracker", [])
                activity = _bucket_get(data, EMAIL_ACTIVITY_KEY, [])
            if not settings or not settings.get("smtpHost"):
                self._send_json(400, {"error": "Email account is not configured yet."})
                return
            supplier = next((s for s in suppliers if s.get("id") == supplier_id), None)
            if not supplier:
                self._send_json(404, {"error": "supplier entry not found"})
                return
            to_addr = (supplier.get("contactEmail") or "").strip()
            if not to_addr:
                self._send_json(400, {"error": "This supplier has no contact email saved."})
                return
            try:
                send_plain_email(settings, to_addr, subject, notify_body)
            except Exception as e:
                self._send_json(502, {"error": f"Send failed: {e}"})
                return
            activity.insert(0, {
                "id": "act_" + uuid.uuid4().hex[:10],
                "at": time.time(),
                "type": "supplier_notify",
                "subject": subject,
                "to": to_addr,
            })
            with _lock:
                data = _load_data()
                _bucket_set(data, EMAIL_ACTIVITY_KEY, activity[:200])
                _save_data(data)
            self._send_json(200, {"ok": True})
            return

        self.send_error(404, "Not found")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"PPWR Compliance Desk is running at {url}")
    print(f"Data file: {DATA_FILE}")
    print("Keep this window open while you use the app. Press Ctrl+C to stop.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        server.shutdown()


if __name__ == "__main__":
    main()
