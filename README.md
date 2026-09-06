# PPWR Compliance Desk
 
A self-contained local app for generating and tracking EU **PPWR**
(Packaging & Packaging Waste Regulation, Regulation (EU) 2025/40)
compliance paperwork — Declarations of Conformity, customer requests,
supplier documentation, and now an email layer that reads incoming
mail and keeps the trackers up to date on its own.
 
Built because the hard part of PPWR compliance usually isn't
understanding the regulation — it's the paperwork trail around it:
generating a correct Declaration, keeping track of which customer
asked for which SKU, chasing suppliers for missing documents, and
not losing any of it in an email inbox.
 
---
 
## Table of contents
 
- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Running it](#running-it)
- [Tour of the app](#tour-of-the-app)
  - [Generate DoC](#generate-doc)
  - [Document Library](#document-library)
  - [Customer Requests](#customer-requests)
  - [Supplier Tracker](#supplier-tracker)
  - [Email Inbox](#email-inbox)
  - [Dashboard](#dashboard)
- [How it's built](#how-its-built)
- [Where your data lives](#where-your-data-lives)
- [Multiple people, one shared copy](#multiple-people-one-shared-copy)
- [Folder contents](#folder-contents)
- [Security notes](#security-notes)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
---
 
## What it does
 
Six connected views over one shared set of data:
 
| Tab | What it's for |
|---|---|
| **Generate DoC** | Fill in a form, get a live preview of an Annex VIII–format Declaration of Conformity, checked against the applicable PPWR articles as you go |
| **Document Library** | Every Declaration you've saved, searchable, ready to duplicate for a new SKU |
| **Customer Requests** | Every customer ask for a Declaration, from first request to delivery, with overdue flags |
| **Supplier Tracker** | What each supplier has sent you, what's missing, and when to follow up |
| **Email Inbox** | Connects to a real mailbox, reads incoming mail, drafts or sends replies, and logs correspondence into the two trackers above automatically |
| **Dashboard** | Open/overdue counts, a status breakdown, and a recent-activity feed across everything |
 
No account, no cloud service, no API keys anywhere in the stack —
it's a small Python server (standard library only) plus a single
HTML/JS front end, and everything is saved to a plain file next to
the app.
 
## Requirements
 
- **Python 3.8+** — already installed on most Macs and Linux
  machines. On Windows, install it once from
  [python.org/downloads](https://www.python.org/downloads/) and tick
  **"Add python.exe to PATH"** during setup.
That's the only requirement. Every feature — including the email
IMAP/SMTP integration — uses Python's standard library, so there's
nothing to `pip install`.
 
## Running it
 
- **Windows:** double-click `Start PPWR Compliance Desk.bat`.
- **macOS / Linux:** double-click `start.sh`, or run `./start.sh` in
  a terminal (first time only, you may need `chmod +x start.sh`).
A console window opens and your browser launches automatically to
`http://127.0.0.1:8743/`. Leave the console window open while you
work — closing it stops the app.
 
---
 
## Tour of the app
 
### Generate DoC
 
A two-column form: fill in fields on the left (document number,
revision, issue date, trade designation, SKU, issuer details), and a
live preview on the right renders it as a proper Annex VIII–laid-out
document, page by page, as you type.
 
Behind the fields sits an extensive built-in checklist of PPWR
articles — substance restrictions (Art. 5), design-for-recycling and
recyclability grading (Art. 6), recycled-content targets (Art. 7),
compostability (Art. 9), minimisation (Art. 10), reuse-system design
(Art. 11), labelling (Art. 12), and more — each with a conformity
status and a free-text reference field, so the generated document
reflects which requirements actually apply to that specific product
rather than a generic template. Save your issuer profile once and
every new document starts pre-filled with your company details.
 
`Save to library`, `Duplicate` for a new SKU, or `Print / Save as
PDF` straight from the browser's print dialog.
 
### Document Library
 
Every saved Declaration in one searchable table — trade designation,
SKU, company, document number, last updated — with **Open**,
**Duplicate**, and **Delete** on each row.
 
### Customer Requests
 
Tracks every customer ask for a Declaration from request to
delivery: customer, packaging/SKU, requested date, due date, status
(**Open** / **Sent** / **Overdue**, calculated automatically), who's
assigned, and free-text notes. Add manually with **+ New request**,
or let the Email Inbox populate it automatically (see below).
**Export CSV** any time.
 
### Supplier Tracker
 
The mirror image, for documents you're waiting on *from* suppliers:
supplier, document type, related SKU, date received, completeness
(**Not received** / **Incomplete** / **Complete**, set manually
after you've actually checked), missing items, and a follow-up date.
Optionally add a **Contact email**, which unlocks two things: the
Email Inbox can recognise their emails by sender domain even if
their name isn't mentioned, and editing an entry offers to send them
a follow-up email referencing what's missing (see below).
 
### Email Inbox
 
Connects to any mailbox over IMAP (to read) and SMTP (to reply/send)
using standard settings — Gmail, Outlook/365, IONOS, GMX, Web.de,
Strato, or any other provider. This is the layer that closes the gap
between "an email arrived" and "it's logged somewhere useful."
 
**Setup:** click **Configure mailbox**, enter your IMAP/SMTP host,
port, username, and an app-specific password (most providers require
one instead of your normal login password once 2FA is on — search
"[your provider] app password" if unsure). **Test connection** before
saving.
 
**What happens on "Check for new emails":**
 
1. **Automated senders are always skipped** — no-reply addresses,
   newsletters, notification services — regardless of what the
   subject line says.
2. **Supplier match first.** If the sender's name or email domain
   matches an existing Supplier Tracker entry, the email is logged
   straight there — document type guessed from the text (PFAS,
   REACH, recycled content, material declaration, test report,
   declaration of conformity, safety data sheet, recyclability — all
   matches captured, not just the first one), SKU pulled from the
   text, a follow-up date (an explicit date the sender mentioned, or
   one week out by default), completeness starting as "Incomplete"
   until you verify it. No reply is drafted — this only records
   correspondence.
3. **Otherwise, customer classification.** Checked against your
   Document Library and Customer Requests:
   - **Routine** — clearly names a product/SKU you already have a
     saved Declaration for, and reads like a simple "please send it"
     request. A reply drafts itself with that document's number,
     revision, issue date, and recyclability grade.
   - **Needs response** — compliance-related, but no confident match
     or a more open-ended question. A generic draft is prepared for
     you to build on.
   - **Not PPWR-related** — hidden by default.
4. **Every draft waits for a human by default.** Turn on **Auto-send**
   in the mailbox settings and only the *Routine* category sends
   without review — everything else still lands in your queue either
   way.
5. **Routine and Needs response emails auto-log into Customer
   Requests** — SKU, a due date (parsed from the email if the sender
   wrote one, in nearly any common format, otherwise one week out),
   and a note linking back to the source email.
6. **Attachments are saved**, not just text — any real file attached
   to a relevant email (PDF, scan, whatever) is stored locally and
   stays one click away from the Email Inbox entry and the tracker
   row it created. The app never opens or reads what's inside them;
   that's still on you.
7. **Re-check match** — if you save a matching document *after* an
   email already arrived and got classified, open that email and
   click this to re-evaluate it against your now-current library,
   without needing to resend anything.
8. **Supplier follow-ups** — editing an existing Supplier Tracker
   entry and saving offers (via a confirmation popup) to send that
   supplier a follow-up email built from the entry's document type,
   SKU, missing items, and notes.
### Dashboard
 
Four headline counts (declarations in library, open customer
requests, overdue requests, incomplete supplier docs), a status
breakdown bar chart across both trackers, and a recent-activity feed
pulling from both Customer Requests and Supplier Tracker.
 
---
 
## How it's built
 
- **Backend:** a single `app.py`, using only Python's standard
  library (`http.server`, `imaplib`, `smtplib`, `email`, `json`) —
  no `pip install` needed, no external services called at any point.
- **Frontend:** a single `static/index.html` — vanilla HTML, CSS, and
  JavaScript, no build step, no framework.
- **Storage:** a simple key/value JSON store the frontend reads and
  writes via a couple of local API endpoints, backed by
  `data.json` on disk.
- **Classification (email → routine/needs-response/supplier/etc.):**
  plain keyword and pattern matching, entirely offline — no AI model
  or external API call is involved anywhere in the email pipeline.
## Where your data lives
 
Everything you save — issuer profile, generated documents, customer
requests, supplier records — is written to **`data.json`** in this
folder, in plain readable JSON. Email attachments are the exception:
they're saved as real files under an **`attachments/`** folder next
to it, since binary files don't belong in JSON.
 
- **Back it up** by copying `data.json`, or use **Export backup** in
  the sidebar (downloads a dated `.json` file). Export backup does
  **not** include `attachments/` — copy that folder separately if
  you need the actual files preserved.
- **Move to another computer** by copying the whole folder, or use
  **Export backup** / **Import backup** for a clean transfer (then
  copy `attachments/` by hand if needed).
- **Share with a colleague** the same way — the folder, or just the
  backup file if they already have the app and don't need the
  underlying attachment files.
## Multiple people, one shared copy
 
Putting this folder on a shared network drive doesn't make it
multi-user — the server only listens on `127.0.0.1` (your own
machine), so each person running `app.py` gets their own local
`data.json`. For genuinely shared use: one person runs the app and
uses **Export backup** to hand snapshots to teammates, or host
`app.py` on a shared server everyone points their browser at.
 
## Folder contents
 
```
ppwr-app/
├── app.py                          ← the local server (Python stdlib only)
├── static/index.html               ← the app itself (UI, forms, PPWR logic)
├── Start PPWR Compliance Desk.bat  ← Windows launcher
├── start.sh                        ← macOS/Linux launcher
├── data.json                       ← created automatically on first save
├── attachments/                    ← saved email attachments, one folder per email
└── README.md                       ← this file
```
 
## Security notes
 
- **Mailbox credentials are stored in plain text** in `data.json`,
  the same way as the rest of the app's data — not encrypted. Use an
  app-specific password where your provider supports one, and be
  careful about where this folder ends up (don't commit it to a
  public GitHub repo, don't put it on a shared drive without
  thinking about who else can read it).
- **`data.json`, `data.json.corrupt-backup`, `attachments/`, and
  `__pycache__/` should be in your `.gitignore`** if you version
  this project with Git — the second file is created automatically
  if the app ever finds a corrupted data file at startup, and it can
  contain everything the corrupted file did, credentials included.
- The server only binds to `127.0.0.1`, so nothing on your network
  can reach it unless you deliberately change that.
- Every SMTP send — auto-reply, manual reply, or supplier follow-up
  — either requires your explicit click or a one-time opt-in
  (auto-send), and the confirmation for a supplier follow-up shows
  you the exact email before it goes out.
## Known limitations
 
- **The email classifier is rule-based, not AI** — plain keyword and
  phrase matching, entirely offline. It can occasionally miss an
  unusually worded request or misfire on a coincidental match. Worth
  glancing through "Needs response" and "Not PPWR-related" (via
  "Show all") periodically, especially early on.
- **Attachments are saved but never read.** The app can't verify a
  PDF actually contains a valid PFAS statement — completeness always
  starts as "Incomplete"/unverified, by design, until a person checks.
- **No live PDF generation for auto-replies.** A Routine reply
  includes the declaration's key facts as text, not an attached PDF;
  it invites the requester to ask for the signed document separately.
- **Classification happens once**, at first fetch, against your
  library/trackers as they stood at that moment. Adding a matching
  document afterward doesn't retroactively fix already-classified
  emails on its own — use **Re-check match**.
- **Numeric dates are read day-first** (the European convention:
  `12.09.2026` = 12 September), which will misread US-style
  `MM/DD/YYYY` dates if a sender happens to use that format.
## Troubleshooting
 
- **"Python was not found"** — install Python 3 from python.org and
  make sure "Add to PATH" was checked, then try again.
- **Browser doesn't open automatically** — open
  `http://127.0.0.1:8743/` yourself while the console window is
  running.
- **Port already in use** — another program is using port 8743.
  Close it, or open `app.py` and change `PORT = 8743` to another
  number (e.g. `8744`), then restart.
- **Sidebar shows "Saving to this browser only"** instead of "Saving
  to local data file" — the app couldn't reach its own server,
  usually because `static/index.html` was opened directly instead of
  through the launcher. Always start it via the `.bat`/`start.sh`
  file.
- **IMAP/SMTP test fails** — double-check host, port, and whether
  your provider needs SSL vs. STARTTLS on that port (993 is almost
  always SSL, 587 is almost always STARTTLS). Also confirm IMAP
  access itself is switched on in your provider's mail settings —
  some providers disable it by default.
 
