# PPWR Compliance Desk — local app

A self-contained tool for generating EU PPWR (Packaging & Packaging
Waste Regulation) Declarations of Conformity, plus a document
library, customer-request tracker, supplier-document tracker, email
inbox, and dashboard.

It runs the same way on every computer: a tiny local server (built
into `app.py`, no installation beyond Python itself) serves the app
in your default browser and saves every change to a plain file,
`data.json`, next to the app. No account, no internet connection,
no browser-storage quirks.

## Requirements

- **Python 3.8+** (already installed on most Macs and Linux
  machines; on Windows, install it once from
  [python.org/downloads](https://www.python.org/downloads/) and
  tick **"Add python.exe to PATH"** during setup).

That's the only requirement — everything else is Python's standard
library, so there's nothing to `pip install`.

## Running it

- **Windows:** double-click `Start PPWR Compliance Desk.bat`.
- **macOS / Linux:** double-click `start.sh`, or run `./start.sh`
  in a terminal. (First time only, you may need
  `chmod +x start.sh`.)

A console window opens and your browser launches automatically to
`http://127.0.0.1:8743/`. Leave the console window open while you
work — closing it stops the app. Close it (or press Ctrl+C in it)
when you're done.

## Email inbox

The **Email Inbox** tab connects to a mailbox over IMAP (to read) and
SMTP (to reply) using any provider's standard settings — Gmail,
Outlook/365, IONOS, GMX, Web.de, Strato, a business host, etc. It
uses only Python's built-in libraries, so there's nothing extra to
install; you just need your host's IMAP/SMTP hostnames and, for most
providers, an **app-specific password** rather than your normal
login password (Gmail, Outlook, GMX and others require this when
2-factor authentication is on — search "[your provider] app
password" if you're not sure).

**How it works:**
1. Open the tab, click **Configure mailbox**, fill in your IMAP/SMTP
   details, and click **Test connection** before saving.
2. Click **Check for new emails** any time to pull in recent
   messages (default: last 14 days, most recent 60).
3. Before checking for a customer ask, each message is first checked
   against your **Supplier Documentation Tracker** — see "Supplier
   emails" below. Automated senders (no-reply addresses, newsletters,
   notification services) are always skipped for both paths,
   regardless of wording. If it isn't from a known supplier, it's
   then checked against your Document Library and Customer Requests
   for a PPWR-related ask:
   - **Routine** — the message clearly names a product/SKU that's
     already in your library (matched as a whole word/phrase, so a
     short SKU can't accidentally match inside an unrelated word)
     and reads like a simple "please send the declaration" request.
     A reply with that document's key details is drafted
     automatically.
   - **Needs response** — PPWR/packaging-related, but no confident
     match or a more open-ended question. A generic starting draft
     is prepared for you to edit. If the email mentions a SKU that
     isn't in your library yet (e.g. "SKU ABC-123"), that reference
     is still pulled into the logged request's SKU field, flagged as
     unverified.
   - **Not PPWR-related** — hidden by default; tick "Show all" to
     see them too.
4. By default, **every** draft waits for you to review and press
   **Send reply**. If you turn on **Auto-send** in the mailbox
   settings, only the *Routine* category is sent automatically and
   without further review — everything else still lands in your
   queue. You'll get a one-time confirmation prompt when you switch
   auto-send on.
5. Every message classified as *Routine* or *Needs response* is also
   logged as a new entry in the **Customer Requests** tab
   automatically — customer name (matched tracker entry, or the
   sender if none matched), SKU if a document was matched or
   mentioned in the text, a due date, and a note pointing back to the
   source email. Its status starts as "Open" and flips to "Sent"
   once a reply actually goes out (auto or manual). You can edit or
   delete these like any other tracker entry afterwards.
6. **Due date:** if the sender wrote an explicit deadline near a cue
   word like "by", "before", "no later than", or "deadline" — in any
   of these forms: `12th of September, 2026` / `September 12, 2026`
   / `Sep 12, 2026` / `12.09.2026` / `12/09/2026` / `2026-09-12` — the
   app uses that date and flags it in the Notes as detected-not-
   verified. Numeric day/month/year dates are read the European way
   (day first). If no explicit deadline is found, it defaults to one
   week from today instead.
7. Classification only happens once, when a message is first fetched
   — it checks your Document Library and Customer Requests exactly
   as they stood at that moment. If you save the matching document
   *after* an email already came in and got classified, the app
   won't automatically look again (it never re-processes a message
   it's already seen). Open that email via "View & reply" and click
   **Re-check match** to have it re-evaluated against your
   up-to-date library — it'll refresh the classification, the draft,
   and the linked Customer Requests entry's SKU, without sending
   anything on its own.

### Supplier emails

If an incoming email is recognised as coming from a supplier already
on your **Supplier Documentation Tracker**, it's handled completely
separately from the customer pipeline above — logged straight to
that tracker, with no reply drafted (this is about recording
correspondence, not answering it).

**How a supplier is recognised** — checked in this order, first
match wins:
1. The supplier's name (from an existing tracker entry) appears in
   the email's subject/body, or in the sender's display name.
2. The domain of the sender's address matches the domain of a
   **Contact email** you've saved on one of their tracker entries
   (add this in the supplier form — it's optional, but needed for
   domain matching to work at all).

**What gets logged**, as a new Supplier Tracker row:
- **Supplier** — the matched entry's name.
- **Document type** — guessed from the email text (recognises
  mentions of PFAS, REACH, recycled content, material declarations,
  test reports, declarations of conformity, safety data sheets,
  recyclability); falls back to the matched entry's existing
  document type if nothing is recognised.
- **SKU** — read from the text the same way as the customer side
  (e.g. "SKU ABC-123"), or carried over from the matched entry.
- **Received date** — today.
- **Completeness** — always starts as "Incomplete," since the app
  can't verify an actual attachment; the Notes say so explicitly and
  you update it once you've checked.
- **Follow-up date** — an explicit date mentioned in the email if
  there is one (same parsing as the customer side's due dates),
  otherwise one week out.

Because this only logs correspondence rather than answering it, the
email shows a "Supplier update" badge and a "Logged to Supplier
Tracker" status, with a shortcut button to jump straight to that tab
instead of a reply box.

### Follow-up emails to suppliers

Whenever you **edit an existing** Supplier Tracker entry and click
**Save changes** — whether you changed the Missing items, the
notes, the completeness status, or anything else — the app asks (via
a confirmation popup) whether to send that supplier a follow-up
email, as long as they have a **Contact email** saved. Say yes and
it drafts and sends immediately using the entry's document type,
SKU, missing items and notes; say no and nothing goes out. This
never fires when creating a brand-new entry, only on edits to one
that already exists, and there's no separate review step before
sending — the confirmation popup itself shows you exactly what will
be sent, so read it before confirming.

### Attachments

Any actual file attached to an email — a PDF, a scanned certificate,
whatever the sender sent — is saved locally (in an `attachments/`
folder next to `data.json`, one subfolder per email) whenever the
email is classified as *Routine*, *Needs response*, or a supplier
update. Attachments on emails classified *Not PPWR-related* aren't
saved, to avoid quietly filling the folder with unrelated mail.

You can open or download an attachment from two places:
- **Email Inbox** — open the email via "View" / "View & reply"; a
  list of its attachments appears with the file name and size.
- **Customer Requests / Supplier Tracker** — a small 📎 icon appears
  in the **Files** column on any row that was auto-logged from an
  email with an attachment. Hover it to see the file name, click to
  open it in a new tab.

This is view-only — the app never opens, scans, or reads the
contents of an attachment, so it still can't tell you whether the
document inside actually satisfies what was requested. That's why
completeness still starts as "Incomplete"/unverified either way:
attachments make it possible for *you* to check, not automatic.
Combined attachments per email are capped at 15 MB; anything beyond
that is skipped rather than filling up the folder.

**Things worth knowing:**
- The classifier is simple keyword/phrase matching, not AI — it
  runs entirely offline and can occasionally miss a match or
  mis-file something, so it's worth glancing through "Needs
  response" and "Not PPWR-related" occasionally, especially early on.
- Auto-replies include the declaration's key facts (document number,
  revision, issue date, SKU, recyclability grade) as text, not an
  attached PDF — the app can't generate a PDF on its own in the
  background, so it invites the requester to ask for the signed
  document if they need it.
- Your mailbox password is stored in the same plain-text
  `data.json` as everything else in this app — see the security note
  in the app itself. It is **never** included in **Export backup**
  files, so restoring a backup on a new machine will ask you to
  re-enter it.

## Where your data lives

Everything you save — issuer profile, generated documents, customer
requests, supplier records — is written to **`data.json`** in this
same folder, in plain readable JSON. Email attachments are the one
exception: they're saved as real files under an **`attachments/`**
folder next to it, since binary files don't belong in a JSON file.
That means:

- **Back it up** by copying `data.json` (or use the in-app
  **Export backup** button in the sidebar, which downloads a dated
  `.json` file). Export backup does **not** include the
  `attachments/` folder — copy that separately if you need the
  actual files preserved too.
- **Move to another computer** by copying this whole folder —
  `data.json` and `attachments/` both travel with it, so your data
  and saved files come too. If you'd rather start clean on a new
  machine and bring data over afterward, use **Export backup** on
  the old machine and **Import backup** on the new one (both buttons
  are in the left sidebar) — then copy `attachments/` across by hand
  if you need those files as well.
- **Share with a colleague** the same way — send them the folder
  (including `attachments/` if relevant), or just the exported
  backup file if they already have the app installed and don't need
  the underlying files.

## Multiple people, one shared copy

If you put this folder on a shared network drive and multiple
people run `app.py` from their own machines pointing at the same
folder, they'd each get their own local `data.json` (the server
only listens on `127.0.0.1`, i.e. your own machine). For a
genuinely shared/multi-user setup, the cleanest approach is: one
person runs the app and generates/saves records, then uses
**Export backup** to hand a snapshot to teammates, or you host
`app.py` on a shared server everyone points their browser at
(ask if you'd like help setting that up).

## Folder contents

```
ppwr-app/
├── app.py                          ← the local server (just Python stdlib)
├── static/index.html               ← the app itself (UI, forms, PPWR logic)
├── Start PPWR Compliance Desk.bat  ← Windows launcher
├── start.sh                        ← macOS/Linux launcher
├── data.json                       ← created automatically on first save
└── README.md                       ← this file
```

## Troubleshooting

- **"Python was not found"** — install Python 3 from
  python.org and make sure "Add to PATH" was checked, then try
  again.
- **Browser doesn't open automatically** — open it yourself at
  `http://127.0.0.1:8743/` while the console window is running.
- **Port already in use** — another program is using port 8743.
  Close it, or open `app.py` in a text editor and change
  `PORT = 8743` to another number (e.g. `8744`), then restart.
- **Sidebar shows "Saving to this browser only"** instead of
  "Saving to local data file" — this means the app couldn't reach
  its own server, usually because you opened `static/index.html`
  directly instead of running it via the `.bat`/`start.sh`
  launcher. Always start it through the launcher.
