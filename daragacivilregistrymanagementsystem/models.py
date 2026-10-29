from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    password_vault = db.Column(db.Text)  # encrypted copy so admin can view staff passwords
    role = db.Column(db.String(32), nullable=False)  # "ADMIN" or "STAFF"
    session_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)
    photo_path = db.Column(db.String(256), nullable=False, default="")


class Record(db.Model):
    """Stored civil registry record (saved when user confirms form)."""
    __tablename__ = "records"
    id = db.Column(db.Integer, primary_key=True)
    document_type = db.Column(db.String(32), nullable=False)  # birth, marriage, death
    registry_number = db.Column(db.String(64))
    full_name = db.Column(db.String(256))
    event_date = db.Column(db.String(32))  # stored as string e.g. YYYY-MM-DD or "15 MAY 1990"
    data_json = db.Column(db.Text, nullable=False)  # all form fields as JSON
    extracted_json = db.Column(db.Text, nullable=False, default="{}")  # copy of submitted/form data for compatibility
    corrected_json = db.Column(db.Text, nullable=False, default="{}")  # corrected/edited form data for compatibility
    image_path = db.Column(db.String(512))  # relative path under uploads, e.g. birth/birth_foo.jpg
    image_front_path = db.Column(db.String(512), nullable=False, default="")  # front scan path for compatibility
    status = db.Column(db.String(32), nullable=False, default="archived")  # e.g. archived, pending
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RecordAttachment(db.Model):
    """Supporting paper stored in this record's client folder. Can be added many times."""
    __tablename__ = "record_attachments"
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name = db.Column(db.String(255), nullable=False, default="")
    stored_path = db.Column(db.String(512), nullable=False, default="")
    kind = db.Column(db.String(64), nullable=False, default="Other")
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    record = db.relationship(
        "Record",
        backref=db.backref("attachments", lazy="dynamic", cascade="all, delete-orphan"),
    )


class EditRequest(db.Model):
    """Staff request to edit a record; admin approves and issues a 5-digit code for staff to use."""
    __tablename__ = "edit_requests"
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="PENDING")  # PENDING, APPROVED, REJECTED
    reason = db.Column(db.Text, nullable=False, default="")
    fields_requested = db.Column(db.String(512), nullable=False, default="")  # comma-separated field names
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)  # for DB compatibility
    reviewed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approval_code = db.Column(db.String(10), nullable=False, default="")  # 5-digit code when approved
    code_used_at = db.Column(db.DateTime)  # when staff used the code to complete edit
    rejection_reason = db.Column(db.Text, default="")  # optional reason when admin rejects
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)  # for DB compatibility

    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    record = db.relationship("Record", backref=db.backref("edit_requests", lazy="dynamic"))


class PrintRequest(db.Model):
    """Staff request to print a record; admin must approve before printing."""
    __tablename__ = "print_requests"
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="PENDING")  # PENDING, APPROVED, REJECTED
    reason = db.Column(db.Text, nullable=False, default="")
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    rejection_reason = db.Column(db.Text, default="")
    used_at = db.Column(db.DateTime)  # set when staff completes a permitted print
    approval_code = db.Column(db.String(10), nullable=False, default="")  # 5-digit code when approved
    # original = scanned certificate; certification = CR Form 1A / 2A / 3A
    print_format = db.Column(db.String(32), nullable=False, default="original")

    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    record = db.relationship("Record", backref=db.backref("print_requests", lazy="dynamic"))


class AuditLog(db.Model):
    """Admin audit log for key actions."""
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(128), nullable=False)
    details = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])


class PrintLog(db.Model):
    """Log of staff printing documents (viewable by admin)."""
    __tablename__ = "print_logs"
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    printed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    record = db.relationship("Record", backref=db.backref("print_logs", lazy="dynamic"))
    user = db.relationship("User", backref=db.backref("print_logs", lazy="dynamic"))


class BackupRun(db.Model):
    """History of automatic date-based document backups (does not change original records)."""
    __tablename__ = "backup_runs"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), nullable=False)  # daily, monthly, yearly
    period_key = db.Column(db.String(32), nullable=False)  # 2026-09-05 / 2026-09 / 2026
    status = db.Column(db.String(16), nullable=False, default="success")  # success, skipped, error
    file_rel = db.Column(db.String(512), nullable=False, default="")
    record_count = db.Column(db.Integer, nullable=False, default=0)
    message = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class OfficeMessage(db.Model):
    """Direct office message between a staff account and an administrator."""
    __tablename__ = "office_messages"
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime)

    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])


class NotificationRead(db.Model):
    """Per-user read/unread state for header notification items."""
    __tablename__ = "notification_reads"
    __table_args__ = (
        db.UniqueConstraint("user_id", "notice_id", name="uq_notification_reads_user_notice"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    notice_id = db.Column(db.String(128), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
