# Daraga Civil Registry Management System  
## Full Feature & Function Reviewer (for QNA / Defense)

**Project:** Daraga Civil Registry Management System (PostgreSQL)  
**LGU:** Municipality of Daraga (Locsin), Albay — Office of the Municipal Civil Registrar  
**Purpose of this document:** Study guide for groupmates (features, functions, workflows, and sample Q&A)

---

## 1. What is the system?

A **local / offline web application** for the Municipal Civil Registry of Daraga that:

1. **Scans or uploads** birth, marriage, and death certificates  
2. Runs **OCR** (Optical Character Recognition) on this computer (not cloud)  
3. Lets staff **review and correct** extracted data  
4. **Archives** confirmed records in **PostgreSQL**  
5. Supports **search**, **print**, **edit requests**, **reports**, and **automatic backup**

**Problem it solves:** Paper certificates are hard to search and manage. This system digitizes them, stores structured data, and keeps an audit trail for municipal use.

**How to open:** After starting PostgreSQL and `python app.py`, open **http://127.0.0.1:5001**

**Default accounts (change after setup):**
| Role  | Username | Password  |
|-------|----------|-----------|
| Admin | admin    | admin123  |
| Staff | staff    | staff123  |

---

## 2. Technology stack (common QNA topic)

| Layer | Technology | Why it matters |
|-------|------------|----------------|
| Web framework | **Flask** (Python) | Serves pages and APIs |
| Templates | **Jinja2** HTML | UI pages |
| Database | **PostgreSQL** | Stores users, records, requests, audit, backups history |
| ORM | **SQLAlchemy / Flask-SQLAlchemy** | Database models |
| OCR | **PaddleOCR** + OpenCV | Reads text from certificate images locally |
| OCR jobs | Separate **OCR worker** process | Keeps the website responsive while OCR runs |
| Scheduler | **APScheduler** | Automatic daily/monthly/yearly/full backups |
| Reports PDF | **fpdf2** | Official register PDF download |
| Windows scanner | **WIA** (pywin32) | Scan from printer/scanner on Windows |
| Config | **`.env`** + `python-dotenv` | Database URL, secret key, backup server path |

**Important talking point:** The system is designed as an **offline local vault** — OCR and data stay on the municipal machine/network; it is not dependent on cloud OCR APIs.

---

## 3. User roles and permissions

### 3.1 Staff
Can:
- Use **Scan Workflow** (upload/scan + OCR)
- Open **Auto-Filled Form** (latest review)
- View **Archiving** and **Search Records**
- View a record’s details
- **Request edit** and **Request print**
- Track **My Edit Requests** and **My Print Requests**
- Use a **5-digit approval code** (after admin approval) to edit
- Print only when an approved print request is still unused
- Manage **My account** (password, sign out other devices)

Cannot:
- Open **Administration**
- Open **Civil Registry Reports**
- Freely edit/print without approval
- Delete archived records
- Manage users or backup settings

### 3.2 Admin
Everything staff can do, plus:
- **Edit record** and **Print document** directly
- **Delete** records (Archiving)
- **Document annotations** (save/remove)
- **Civil Registry Reports** (print / PDF / Excel)
- **Administration** tabs: Dashboard, Edit requests, Print requests, Print logs, Audit log, Users, Automatic Backup
- Approve / reject staff requests
- Create users, set/issue passwords, delete accounts (at least one ADMIN must remain)

---

## 4. Main menu (sidebar) — features map

| Menu item | Who | Function |
|-----------|-----|----------|
| **Scan Workflow** | Both | Scan/upload certificate → detect type → Run OCR |
| **Auto-Filled Form** | Both | Reopen the latest OCR review form |
| **Archiving** | Both | Browse saved Birth / Marriage / Death records |
| **Search Records** | Both | Search by type, registry number, name, mother’s name, date |
| **My Edit Requests** | Staff | Status of edit requests |
| **My Print Requests** | Staff | Status of print requests |
| **Civil Registry Reports** | Admin | Official registers + Print / PDF / Excel |
| **Administration** | Admin | Users, approvals, audit, backup, dashboard |
| **My account** | Both | Profile, change password, revoke sessions |
| **Logout** | Both | End session |

---

## 5. Core workflow: Scan → OCR → Confirm → Archive

### Step-by-step (memorize this for defense)

1. **Login** as staff or admin  
2. Open **Scan Workflow**  
3. **Scan from printer** or **Upload image** (JPG/PNG)  
4. System **auto-detects** document type (Birth / Marriage / Death); user can override if wrong  
5. Click **Run OCR** (progress shows; job can be **Cancelled**)  
6. Open the **Review** page (Birth / Marriage / Death)  
7. Correct fields if OCR mistakes appear (confidence badges: HIGH / MEDIUM / LOW)  
8. Optionally add **Document annotation**  
9. Click **Confirm … data**  
10. Record is **saved** in the database and appears under **Archiving** / **Search**

**Key rule:** Nothing is permanently archived until the user **confirms**. OCR only proposes data.

### Document types and main fields

**Birth (Municipal Form 102)**  
Registry Number, Date of Registration, Name of Child, Sex, Date/Place of Birth, Name of Mother / Father, Citizenship of parents, Date/Place of Marriage of Parents, Remarks, etc.

**Marriage (Form 97)**  
Registry Number, Date of Registration, Date/Place of Marriage, Husband & Wife (Name, Age, Citizenship/Nationality, Civil Status, Father, Mother), etc.

**Death (Form 103)**  
Registry Number, Date of Registration, Name of Deceased, Sex, Age, Civil Status, Nationality, Date/Place of Death, Cause of Death, etc.

### OCR-related UI features
- Auto document-type detection  
- Manual override: Auto-detect / Birth / Marriage / Death  
- Progress % and Cancel  
- Extracted metadata with confidence labels  
- Clear extracted fields (review)  
- Side-by-side **original certificate** preview with zoom  
- Top “OCR running” bar on other pages (hidden on main Scan Workflow because that page has its own progress panel)

---

## 6. Archiving

- Hub page with counts for Birth / Marriage / Death  
- Lists of archived certificates  
- **View** opens the archived record detail  
- Admin can **Delete** (with confirmation)  
- Filter by document type  

**Archived record detail shows:**
- Original scan (zoomable viewer)  
- Extracted metadata (grouped sections, e.g. Shared / Husband / Wife for marriage)  
- Actions: Back to Search, Print / Request print, Edit / Request edit  
- Admin annotation tools  
- Staff: **Use approval code** after edit approval  

---

## 7. Search Records

Search filters:
- Document type  
- Registry number  
- Full name  
- Mother’s name  
- Event date  

Results link to **View** (record detail).

---

## 8. Edit and print request system (governance)

### Why it exists
Staff should not freely change or print sensitive civil registry documents. Admin controls approval.

### Edit request flow
1. Staff opens a record → **Request edit** → gives a reason  
2. Admin opens **Administration → Edit requests** → **Approve** or **Reject**  
3. If approved, staff gets a **5-digit code**  
4. Staff opens the record → enters code (**Use code & edit**) → opens edit form  
5. After editing, changes are saved; audit can record the action  

### Print request flow
1. Staff → **Request print** (may choose print format)  
2. Admin → **Print requests** → Approve / Reject  
3. Approved print allows **one** use; another print needs a new request  
4. Admin can print directly without requesting  

### Print formats (when allowed)
- **Original document** (scanned image)  
- **Certification form** — CR Form **1A** (birth), **2A** (death), **3A** (marriage)  
- **Both copies**  
- Paper size options; prints are recorded in **Print logs**

---

## 9. Document annotations

Official remarks kept with the record metadata (not drawn as permanent pixels on the scan unless printed as annotation block).

**Types include:**
- Legitimation by subsequent marriage  
- Adoption  
- Court order / decree  
- Correction of entry  
- Other annotation  

Admin can **Save annotation** or **Remove annotation**.

---

## 10. Administration features

### Dashboard
- Pending edit / print request counts  
- Total records and breakdown (Birth / Marriage / Death)  
- Users / staff counts  
- Charts / quick actions  
- Philippine time display  

### Edit requests / Print requests
- Approve / Reject queues  
- Badge counts for pending items  

### Print logs
- Who printed, when, which record  

### Audit log
- Sign-in, edits, backup events, system actions  

### Users
- Create user (ADMIN / STAFF)  
- Generate / set / issue password  
- Sign out user sessions  
- Delete account (must keep at least one ADMIN)  
- Copy revealed credentials  

### Civil Registry Reports (also own menu)
- Official Birth / Death / Marriage **registers** from saved records  
- Filters: Year, Barangay, Find (name/registry), Rows  
- **Print**, **Download PDF**, **Excel**  
- Focus/zoom on the on-screen register form  

### Automatic Backup (very important for defense)
| Feature | Description |
|---------|-------------|
| Schedule On/Off | Enables automatic jobs |
| Daily | Municipal priority — today’s records (configurable time) |
| Monthly / Yearly | Period backups |
| Full | Database + scans |
| Office server (priority) | Shared folder e.g. `\\DARAGA-SERVER\CivilRegistryBackups` or `DARAGA_BACKUP_ROOT` in `.env` |
| This computer (safety copy) | Always under `backups/` |
| Backup now | Save now / Save and download |
| History | Download / Retry / Delete |
| Restore | Replaces current registry data from a zip (dangerous — confirmed) |

**Talking point:** When the Daraga office server path is set and writable, each backup is **copied to the server as priority**, with a **local safety copy**. If the server is offline, local backup still succeeds (status may show Partial).

---

## 11. Security features

- Login required for app pages  
- Role-based access (`ADMIN` vs `STAFF`)  
- Passwords hashed (**pbkdf2:sha256**)  
- Session with role + session version (password change / “Sign out other devices” invalidates old sessions)  
- Staff edit gated by approval code  
- Staff print gated by approved unused request  
- Admin-only administration and reports  
- `SECRET_KEY` and `DATABASE_URL` from `.env` (should be changed for production)  
- Uploads and backups stay local (not pushed to GitHub)

---

## 12. UX / interface features

- Municipal seal / Daraga branding  
- Light / **Dark** theme toggle  
- **Notifications** bell (edit/print request alerts)  
- In-app **toasts** and **confirm dialogs** (instead of raw browser alerts for many actions)  
- Document viewer with zoom / reset  
- OCR confidence badges  
- Responsive layout with sidebar navigation  
- Status badges (Success / Partial / Failed) on backup history  

---

## 13. Data stored (high level)

Typical database entities include:
- **Users** (username, role, password hash, session version)  
- **Records** (document type, registry number, image path, JSON metadata, timestamps)  
- **EditRequest** / **PrintRequest**  
- **AuditLog** / **PrintLog**  
- **BackupRun** (history of automatic backups)  

Files:
- `uploads/` — scanned certificate images  
- `backups/` — zip archives and settings  

---

## 14. End-to-end scenarios (practice these)

### Scenario A — New birth certificate
Staff scans Form 102 → OCR → reviews Name of Child / Mother / Father → Confirm → appears in Archiving → searchable by registry number.

### Scenario B — Staff needs a correction
Staff Request edit → Admin approves → Staff uses 5-digit code → edits fields → saves.

### Scenario C — Staff needs a certification print
Staff Request print → Admin approves → Staff prints Form 1A/2A/3A or original → Print log recorded.

### Scenario D — End of day backup
Schedule Daily On + office server path set → APScheduler runs → zip saved locally and copied to Daraga server share.

---

## 15. Sample QNA (probable defense questions)

**Q1. What is the main purpose of your system?**  
A: To digitize and manage civil registry certificates (birth, marriage, death) for the Municipality of Daraga using local scanning, OCR, PostgreSQL archiving, controlled printing/editing, reports, and automatic backups.

**Q2. Why offline / local?**  
A: Civil registry data is sensitive. OCR and storage run on the municipal computer/network (local vault), reducing dependence on internet cloud services.

**Q3. Difference between Staff and Admin?**  
A: Staff encode via OCR and must request edit/print. Admin approves requests, edits/prints freely, manages users, reports, audit, and backups.

**Q4. What happens during OCR?**  
A: The image is processed by PaddleOCR; fields are extracted with confidence levels; the user reviews and confirms before the record is archived.

**Q5. Is OCR always 100% correct?**  
A: No. That is why there is a Review page. Users correct mistakes before Confirm. Confidence badges help prioritize checking.

**Q6. How do you prevent unauthorized edits?**  
A: Staff cannot edit directly. They request edit; admin approves and issues a one-time 5-digit code.

**Q7. How do you prevent uncontrolled printing?**  
A: Staff need an approved print request (one use). Admin can print freely. Print logs record activity.

**Q8. What database do you use and why PostgreSQL?**  
A: PostgreSQL for a server-ready municipal deployment (multi-user, durable, suitable for office server), replacing the older SQLite offline-only approach.

**Q9. What is Automatic Backup?**  
A: Scheduled jobs (daily/monthly/yearly/full) that create zip archives. Priority copy goes to the office server path when configured; a safety copy stays on the PC.

**Q10. What if the office server is offline during backup?**  
A: The system still saves locally so data is not lost, and reports that the server priority copy failed (Partial).

**Q11. What are Civil Registry Reports?**  
A: Official-style Birth/Death/Marriage registers generated from archived records, with Print, PDF, and Excel export.

**Q12. What is Document Annotation?**  
A: Official remarks (e.g. legitimation, adoption, correction) saved with the record for LCR use.

**Q13. Name the three certificate types.**  
A: Birth, Marriage, and Death certificates.

**Q14. What is Auto-Filled Form?**  
A: A menu entry to reopen the latest OCR review session so the user can continue correcting/confirming.

**Q15. Security measures?**  
A: Login, hashed passwords, role checks, session invalidation, approval codes for staff edits, gated printing, audit logs, local secrets in `.env`.

**Q16. Can you restore data?**  
A: Yes. Administration → Automatic Backup → Restore from a backup zip (replaces current registry data; pre-restore safety measures apply).

**Q17. Who benefits?**  
A: Municipal Civil Registrar staff (faster encoding/search), admins (control and reports), and citizens indirectly (faster retrieval of registry information).

**Q18. Limitations?**  
A: OCR depends on scan quality/handwriting; some fields may need manual correction; Windows scanner uses WIA; server backup requires a reachable shared folder and permissions.

---

## 16. One-minute elevator pitch (memorize)

> “Our Capstone is the **Daraga Civil Registry Management System**, a local Flask web app with **PostgreSQL**. Staff scan birth, marriage, or death certificates; **PaddleOCR** extracts the fields; users review and confirm; records are archived and searchable. Staff need admin approval to edit or print. Admins manage users, audit logs, official reports, and **automatic daily backups** with priority copy to the municipal server. It digitizes the Civil Registry workflow while keeping sensitive data under local municipal control.”

---

## 17. Quick checklist before QNA

- [ ] Can explain Scan → OCR → Review → Confirm  
- [ ] Can explain Admin vs Staff  
- [ ] Can explain Edit code + Print approval  
- [ ] Can name Birth / Marriage / Death  
- [ ] Can explain PostgreSQL + offline OCR  
- [ ] Can explain Automatic Backup (daily + server priority)  
- [ ] Can explain Reports (Print / PDF / Excel)  
- [ ] Can state limitations honestly (OCR not perfect)

---

*Generated for group QNA preparation based on the Daraga Civil Registry Management System (PostgreSQL) feature set.*
