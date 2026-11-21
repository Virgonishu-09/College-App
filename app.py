import csv
import io
import os
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps
import re
from pypdf import PdfReader
from sqlalchemy import text
import csv
import io
from datetime import date

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

# ---------- SETTINGS ----------
# Secret key: created once and saved in instance/secret.key (nothing to paste).
os.makedirs(app.instance_path, exist_ok=True)
_key_file = os.path.join(app.instance_path, "secret.key")
if not os.path.exists(_key_file):
    with open(_key_file, "w") as fh:
        fh.write(secrets.token_hex(32))
with open(_key_file) as fh:
    app.config["SECRET_KEY"] = fh.read().strip()

# Teacher code: change "1234" here, or set a TEACHER_CODE environment variable.
TEACHER_CODE = os.environ.get("TEACHER_CODE", "1234")

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///college3.db"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024      # 100 MB max
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads")
app.config["MATERIAL_FOLDER"] = os.path.join(app.root_path, "uploads", "materials")
app.config["ASSIGNMENT_FOLDER"] = os.path.join(app.root_path, "uploads", "assignments")
for _folder in (app.config["UPLOAD_FOLDER"], app.config["MATERIAL_FOLDER"],
                app.config["ASSIGNMENT_FOLDER"]):
    os.makedirs(_folder, exist_ok=True)

ALLOWED_EXT = {"pdf", "doc", "docx", "txt", "png", "jpg", "jpeg", "zip"}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# ---------- MODELS ----------
class User(UserMixin, db.Model):
    roll_no = db.Column(db.String(30))
    id = db.Column(db.Integer, primary_key=True)
    erp_id = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    branch = db.Column(db.String(30))


class TeacherBranch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    branch = db.Column(db.String(30), nullable=False)


class Lecture(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(80), nullable=False)
    branch = db.Column(db.String(30), nullable=False)
    day = db.Column(db.String(10), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)
    room = db.Column(db.String(20))
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"))


class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    branch = db.Column(db.String(30), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    remarks = db.Column(db.Text)
    attachment = db.Column(db.String(200))
    attachment_name = db.Column(db.String(200))
    submitted_count = db.Column(db.Integer, default=0, nullable=False)


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(200))
    submitted_at = db.Column(db.DateTime, default=datetime.now)
    __table_args__ = (db.UniqueConstraint("assignment_id", "student_id"),)


class Material(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    branch = db.Column(db.String(30), nullable=False)
    filename = db.Column(db.String(200))
    original_name = db.Column(db.String(200))
    uploaded_at = db.Column(db.DateTime, default=datetime.now)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Announcement(db.Model):                       # NEW
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text)
    branch = db.Column(db.String(30), nullable=False)   # "ALL" or one branch
    created_at = db.Column(db.DateTime, default=datetime.now)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class CalendarEvent(db.Model):                      # NEW
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)                       # optional (ranges)
    kind = db.Column(db.String(12), nullable=False)     # holiday/exam/vacation/event
    branch = db.Column(db.String(30), nullable=False)   # "ALL" or one branch
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    branch = db.Column(db.String(30), nullable=False)
    subject = db.Column(db.String(80), nullable=False)
    att_date = db.Column(db.Date, default=date.today, nullable=False)
    status = db.Column(db.String(10), nullable=False)  # "Present" or "Absent"
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("student_id", "subject", "att_date"),)


# ---------- LOGIN HELPERS ----------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def role_required(role):
    """Only lets users with this role open the page."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role != role:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def go_to_dashboard():
    if current_user.role == "teacher":
        return redirect(url_for("teacher_dashboard"))
    return redirect(url_for("student_dashboard"))


def teacher_branches():
    return [tb.branch for tb in
            TeacherBranch.query.filter_by(teacher_id=current_user.id).all()]


def save_upload(f, folder):
    """Validates and saves an optional upload. Returns (stored, original) or
    raises ValueError with a message for the user."""
    if not f or not f.filename:
        return None, None
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_EXT:
        raise ValueError("File type not allowed. Use: " + ", ".join(sorted(ALLOWED_EXT)))
    original = secure_filename(f.filename) or f"file.{ext}"
    stored = f"{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(folder, stored))
    return stored, original

def get_student_attendance(student_id, branch):
    records = Attendance.query.filter_by(student_id=student_id, branch=branch).all()
    by_subject = {}
    total_present = 0
    total_absent = 0

    for r in records:
        if r.subject not in by_subject:
            by_subject[r.subject] = {"present": 0, "total": 0}
        by_subject[r.subject]["total"] += 1
        if r.status == "Present":
            by_subject[r.subject]["present"] += 1
            total_present += 1
        else:
            total_absent += 1

    subject_stats = []
    defaulter_subjects = []
    for subj, data in sorted(by_subject.items()):
        pct = round((data["present"] / data["total"]) * 100, 1) if data["total"] else 0.0
        is_def = pct < 75.0
        item = {"subject": subj, "present": data["present"], "total": data["total"], "pct": pct, "defaulter": is_def}
        subject_stats.append(item)
        if is_def:
            defaulter_subjects.append(item)

    overall_total = total_present + total_absent
    overall_pct = round((total_present / overall_total) * 100, 1) if overall_total else 0.0
    return {
        "subjects": subject_stats,
        "defaulters": defaulter_subjects,
        "present": total_present,
        "absent": total_absent,
        "overall_pct": overall_pct,
    }


def get_class_attendance(branches):
    records = Attendance.query.filter(Attendance.branch.in_(branches)).all()
    students = {s.id: s for s in User.query.filter(User.role == "student", User.branch.in_(branches)).all()}

    # Group by (branch, subject) -> student_id -> {present, total}
    grouped = {}
    for r in records:
        key = (r.branch, r.subject)
        if key not in grouped:
            grouped[key] = {}
        if r.student_id not in grouped[key]:
            grouped[key][r.student_id] = {"present": 0, "total": 0}
        grouped[key][r.student_id]["total"] += 1
        if r.status == "Present":
            grouped[key][r.student_id]["present"] += 1

    class_summary = []
    defaulters_by_subject = []

    for (branch, subject), stu_map in sorted(grouped.items()):
        total_present = sum(v["present"] for v in stu_map.values())
        total_entries = sum(v["total"] for v in stu_map.values())
        avg_pct = round((total_present / total_entries) * 100, 1) if total_entries else 0.0

        def_rolls = []
        for sid, counts in stu_map.items():
            pct = (counts["present"] / counts["total"]) * 100 if counts["total"] else 0.0
            if pct < 75.0 and sid in students:
                stu = students[sid]
                def_rolls.append(stu.roll_no or stu.erp_id)

        class_summary.append({"branch": branch, "subject": subject, "avg_pct": avg_pct})
        if def_rolls:
            defaulters_by_subject.append({
                "branch": branch,
                "subject": subject,
                "rolls": sorted(def_rolls),
            })

    return class_summary, defaulters_by_subject


# ---------- CALENDAR HELPERS ----------
KIND_ICON = {"holiday": "🎉", "exam": "📝", "vacation": "🌴", "event": "📌"}
KIND_ALIASES = {"holiday": "holiday", "exam": "exam", "test": "exam",
                "vacation": "vacation", "break": "vacation", "event": "event"}
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")


def parse_date(text):
    text = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def visible_events(branch):
    return (CalendarEvent.query
            .filter(CalendarEvent.branch.in_(["ALL", branch]))
            .order_by(CalendarEvent.date).all())


def event_end(e):
    return e.end_date or e.date


def event_when(e, today):
    end = event_end(e)
    if e.date <= today <= end:
        return "Today" if e.date == end else "Ongoing"
    days = (e.date - today).days
    return "Tomorrow" if days == 1 else f"in {days} days"


def event_range(e):
    start = e.date.strftime("%d %b")
    if e.end_date and e.end_date != e.date:
        return f"{start} - {e.end_date.strftime('%d %b')}"
    return start


def event_row(e, today, now):
    return {
        "id": e.id, "icon": KIND_ICON.get(e.kind, "📌"), "title": e.title,
        "kind": e.kind, "range": event_range(e), "when": event_when(e, today),
        "detail": e.description or "", "branch": e.branch,
        "new": (now - e.created_at) < timedelta(days=3) if e.created_at else False,
    }


def is_break_today(events, today):
    return any(e.kind in ("holiday", "vacation") and e.date <= today <= event_end(e)
               for e in events)


# ---------- ROUTES: AUTH ----------
@app.route("/")
def home():
    if current_user.is_authenticated:
        return go_to_dashboard()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        erp_id = request.form.get("erp_id", "").strip()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        branch = request.form.get("branch", "").strip().upper()
        code = request.form.get("teacher_code", "").strip()

        if not (erp_id and name and password and branch):
            flash("Please fill every field.", "danger")
        elif role not in ("teacher", "student"):
            flash("Invalid role.", "danger")
        elif role == "teacher" and code != TEACHER_CODE:
            flash("Wrong teacher code.", "danger")
        elif User.query.filter_by(erp_id=erp_id).first():
            flash("This ID is already registered.", "danger")
        else:
            user = User(
                erp_id=erp_id, name=name, role=role,
                password=generate_password_hash(password),
                branch=branch if role == "student" else None,
            )
            db.session.add(user)
            db.session.flush()
            if role == "teacher":                   # "AIML, IT" -> two rows
                for b in branch.split(","):
                    if b.strip():
                        db.session.add(TeacherBranch(teacher_id=user.id,
                                                     branch=b.strip().upper()))
            db.session.commit()
            flash("Registered! Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return go_to_dashboard()
    if request.method == "POST":
        erp_id = request.form.get("erp_id", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(erp_id=erp_id).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return go_to_dashboard()
        flash("Wrong ID or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------- ROUTES: TEACHER ----------
@app.route("/teacher")
@role_required("teacher")
def teacher_dashboard():
    branches = teacher_branches()
    assignments = (Assignment.query
                   .filter_by(teacher_id=current_user.id)
                   .order_by(Assignment.due_date).all())
    rows = []
    for a in assignments:
        total = User.query.filter_by(role="student", branch=a.branch).count()
        rows.append({"a": a, "done": a.submitted_count, "total": total})

    lectures = Lecture.query.filter_by(teacher_id=current_user.id).all()
    materials = (Material.query.filter_by(teacher_id=current_user.id)
                 .order_by(Material.uploaded_at.desc()).all())
    class_summary, defaulters_by_subject = get_class_attendance(branches)

    return render_template(
        "teacher_dashboard.html",
        branches=branches,
        rows=rows,
        lectures=lectures,
        materials=materials,
        class_summary=class_summary,
        defaulters_by_subject=defaulters_by_subject,
    )


@app.route("/teacher/assignment/new", methods=["GET", "POST"])
@role_required("teacher")
def new_assignment():
    branches = teacher_branches()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        remarks = request.form.get("remarks", "").strip()
        branch = request.form.get("branch", "")
        due_text = request.form.get("due_date", "")

        if branch not in branches:                  # branch guard
            abort(403)
        if not title or not due_text:
            flash("Title and due date are required.", "danger")
            return render_template("new_assignment.html", branches=branches)
        try:
            due = datetime.strptime(due_text, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Please pick a valid due date and time.", "danger")
            return render_template("new_assignment.html", branches=branches)
        try:
            stored, original = save_upload(request.files.get("file"),
                                           app.config["ASSIGNMENT_FOLDER"])
        except ValueError as err:
            flash(str(err), "danger")
            return render_template("new_assignment.html", branches=branches)

        db.session.add(Assignment(
            title=title, description=description, remarks=remarks,
            branch=branch, due_date=due, teacher_id=current_user.id,
            attachment=stored, attachment_name=original))
        db.session.commit()
        flash("Assignment created.", "success")
        return redirect(url_for("teacher_dashboard"))
    return render_template("new_assignment.html", branches=branches)


@app.route("/teacher/assignment/<int:assignment_id>")
@role_required("teacher")
def assignment_detail(assignment_id):
    a = db.get_or_404(Assignment, assignment_id)
    if a.teacher_id != current_user.id or a.branch not in teacher_branches():
        abort(403)
    students = (User.query.filter_by(role="student", branch=a.branch)
                .order_by(User.name).all())
    subs = {s.student_id: s for s in
            Submission.query.filter_by(assignment_id=a.id)}
    rows = [{"student": s, "sub": subs.get(s.id)} for s in students]
    done = sum(1 for r in rows if r["sub"])
    return render_template("assignment_detail.html", a=a, rows=rows,
                           done=done, total=len(rows))


@app.route("/assignment/<int:assignment_id>/attachment")
@login_required
def download_attachment(assignment_id):
    a = db.get_or_404(Assignment, assignment_id)
    if current_user.role == "teacher":
        if a.teacher_id != current_user.id:
            abort(403)
    elif a.branch != current_user.branch:           # branch guard
        abort(403)
    if not a.attachment:
        abort(404)
    return send_from_directory(app.config["ASSIGNMENT_FOLDER"], a.attachment,
                               as_attachment=True, download_name=a.attachment_name)


@app.route("/teacher/lecture/new", methods=["GET", "POST"])
@role_required("teacher")
def new_lecture():
    branches = teacher_branches()
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        branch = request.form.get("branch", "")
        day = request.form.get("day", "")
        start_time = request.form.get("start_time", "")
        room = request.form.get("room", "").strip()
        if branch not in branches or day not in DAYS:
            abort(403)
        if not subject or not start_time:
            flash("Subject and time are required.", "danger")
        else:
            db.session.add(Lecture(
                subject=subject, branch=branch, day=day,
                start_time=start_time, room=room, teacher_id=current_user.id))
            db.session.commit()
            flash("Lecture added.", "success")
            return redirect(url_for("teacher_dashboard"))
    return render_template("new_lecture.html", branches=branches, days=DAYS)


@app.route("/teacher/material/new", methods=["POST"])
@role_required("teacher")
def add_material():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    branch = request.form.get("branch", "")
    if branch not in teacher_branches():
        abort(403)
    if not title:
        flash("Please enter a title.", "danger")
        return redirect(url_for("teacher_dashboard"))
    try:
        stored, original = save_upload(request.files.get("file"),
                                       app.config["MATERIAL_FOLDER"])
    except ValueError as err:
        flash(str(err), "danger")
        return redirect(url_for("teacher_dashboard"))
    db.session.add(Material(title=title, description=description, branch=branch,
                            filename=stored, original_name=original,
                            teacher_id=current_user.id))
    db.session.commit()
    flash("Study material added.", "success")
    return redirect(url_for("teacher_dashboard"))


@app.route("/teacher/material/<int:material_id>/delete", methods=["POST"])
@role_required("teacher")
def delete_material(material_id):
    m = db.get_or_404(Material, material_id)
    if m.teacher_id != current_user.id:
        abort(403)
    if m.filename:
        path = os.path.join(app.config["MATERIAL_FOLDER"], m.filename)
        if os.path.exists(path):
            os.remove(path)
    db.session.delete(m)
    db.session.commit()
    flash("Material deleted.", "success")
    return redirect(url_for("teacher_dashboard"))


@app.route("/material/<int:material_id>/download")
@login_required
def download_material(material_id):
    m = db.get_or_404(Material, material_id)
    if current_user.role == "teacher":
        if m.teacher_id != current_user.id:
            abort(403)
    elif m.branch != current_user.branch:
        abort(403)
    if not m.filename:
        abort(404)
    return send_from_directory(app.config["MATERIAL_FOLDER"], m.filename,
                               as_attachment=True, download_name=m.original_name)


# ---------- ROUTES: ANNOUNCEMENTS (teacher) ----------
@app.route("/teacher/announcements", methods=["GET", "POST"])
@role_required("teacher")
def teacher_announcements():
    branches = teacher_branches()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        branch = request.form.get("branch", "")
        if branch != "ALL" and branch not in branches:      # branch guard
            abort(403)
        if not title:
            flash("Please enter a title.", "danger")
        else:
            db.session.add(Announcement(title=title[:150], body=body, branch=branch,
                                        teacher_id=current_user.id))
            db.session.commit()
            flash("Announcement posted.", "success")
            return redirect(url_for("teacher_announcements"))
    mine = (Announcement.query.filter_by(teacher_id=current_user.id)
            .order_by(Announcement.created_at.desc()).all())
    return render_template("teacher_announcements.html", branches=branches, items=mine)


@app.route("/teacher/announcements/<int:item_id>/delete", methods=["POST"])
@role_required("teacher")
def delete_announcement(item_id):
    a = db.get_or_404(Announcement, item_id)
    if a.teacher_id != current_user.id:
        abort(403)
    db.session.delete(a)
    db.session.commit()
    flash("Announcement deleted.", "success")
    return redirect(url_for("teacher_announcements"))


# ---------- ROUTES: ACADEMIC CALENDAR (teacher) ----------
@app.route("/teacher/calendar", methods=["GET", "POST"])
@role_required("teacher")
def teacher_calendar():
    branches = teacher_branches()
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Please choose a CSV file.", "danger")
            return redirect(url_for("teacher_calendar"))
        if not f.filename.lower().endswith(".csv"):
            flash("Please upload a .csv file (in Excel: Save As > CSV).", "danger")
            return redirect(url_for("teacher_calendar"))
        try:
            text = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("Could not read the file. Save it as CSV (UTF-8) and try again.", "danger")
            return redirect(url_for("teacher_calendar"))

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            flash("The file is empty.", "danger")
            return redirect(url_for("teacher_calendar"))
        reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
        if not {"date", "title"}.issubset(reader.fieldnames):
            flash("The first row must contain the columns: date, title "
                  "(optional: end_date, type, branch, description).", "danger")
            return redirect(url_for("teacher_calendar"))

        added = skipped = 0
        errors = []
        for n, row in enumerate(reader, start=2):
            title = (row.get("title") or "").strip()
            raw_date = (row.get("date") or "").strip()
            if not title and not raw_date:
                continue                                   # blank line
            d = parse_date(raw_date)
            if not title or not d:
                errors.append(f"Row {n}: needs a title and a valid date")
                continue
            raw_end = (row.get("end_date") or "").strip()
            end = parse_date(raw_end) if raw_end else None
            if raw_end and (not end or end < d):
                errors.append(f"Row {n}: end_date is not valid")
                continue
            kind = KIND_ALIASES.get((row.get("type") or "").strip().lower(), "event")
            branch = (row.get("branch") or "").strip().upper() or "ALL"
            if branch != "ALL" and branch not in branches:
                errors.append(f"Row {n}: you can't add events for branch {branch}")
                continue
            if CalendarEvent.query.filter_by(title=title, date=d, branch=branch).first():
                skipped += 1
                continue
            db.session.add(CalendarEvent(
                title=title[:150], date=d, end_date=end, kind=kind, branch=branch,
                description=(row.get("description") or "").strip(),
                teacher_id=current_user.id))
            added += 1
        db.session.commit()
        flash(f"{added} event(s) added, {skipped} already existed.", "success")
        for msg in errors[:5]:
            flash(msg, "warning")
        if len(errors) > 5:
            flash(f"...and {len(errors) - 5} more rows had problems.", "warning")
        return redirect(url_for("teacher_calendar"))

    events = (CalendarEvent.query
              .filter(CalendarEvent.branch.in_(["ALL"] + branches))
              .order_by(CalendarEvent.date).all())
    today = datetime.now().date()
    rows = [dict(event_row(e, today, datetime.now()), mine=(e.teacher_id == current_user.id),
                 past=(event_end(e) < today)) for e in events]
    return render_template("teacher_calendar.html", branches=branches, rows=rows)


@app.route("/teacher/calendar/add", methods=["POST"])
@role_required("teacher")
def add_calendar_event():
    branches = teacher_branches()
    title = request.form.get("title", "").strip()
    d = parse_date(request.form.get("date"))
    raw_end = request.form.get("end_date", "").strip()
    end = parse_date(raw_end) if raw_end else None
    kind = KIND_ALIASES.get(request.form.get("kind", ""), "event")
    branch = request.form.get("branch", "")
    if branch != "ALL" and branch not in branches:
        abort(403)
    if not title or not d or (raw_end and (not end or end < d)):
        flash("Please enter a title and valid dates.", "danger")
        return redirect(url_for("teacher_calendar"))
    db.session.add(CalendarEvent(
        title=title[:150], date=d, end_date=end, kind=kind, branch=branch,
        description=request.form.get("description", "").strip(),
        teacher_id=current_user.id))
    db.session.commit()
    flash("Event added.", "success")
    return redirect(url_for("teacher_calendar"))


@app.route("/teacher/calendar/<int:event_id>/delete", methods=["POST"])
@role_required("teacher")
def delete_calendar_event(event_id):
    e = db.get_or_404(CalendarEvent, event_id)
    if e.teacher_id != current_user.id:
        abort(403)
    db.session.delete(e)
    db.session.commit()
    flash("Event deleted.", "success")
    return redirect(url_for("teacher_calendar"))


@app.route("/teacher/calendar/template.csv")
@role_required("teacher")
def calendar_template():
    sample = ("date,end_date,title,type,branch,description\n"
              "2026-12-25,,Christmas,holiday,ALL,College closed\n"
              "2026-12-10,2026-12-20,Semester Exams,exam,ALL,Check the exam timetable\n"
              "2027-01-05,,Unit Test 1,exam,AIML,Chapters 1 to 3\n")
    return Response(sample, mimetype="text/csv", headers={
        "Content-Disposition": "attachment; filename=academic_calendar_template.csv"})


# ---------- ROUTES: STUDENT ----------
def time_left(due):
    delta = due - datetime.now()
    if delta.total_seconds() < 0:
        return "Overdue", True
    hours = delta.seconds // 3600
    if delta.days > 0:
        return f"{delta.days}d {hours}h left", False
    mins = (delta.seconds % 3600) // 60
    return f"{hours}h {mins}m left", False


@app.route("/student")
@role_required("student")
def student_dashboard():
    branch = current_user.branch                    # branch guard
    now = datetime.now()
    today = now.date()
    day_name = now.strftime("%A")

    events = visible_events(branch)
    on_break = is_break_today(events, today)

    lectures = (Lecture.query.filter_by(branch=branch)
                .order_by(Lecture.start_time).all())
    today_lectures = [] if on_break else [l for l in lectures if l.day == day_name]
    week = {d: [l for l in lectures if l.day == d] for d in DAYS}
    faculty = {u.id: u.name for u in User.query.filter_by(role="teacher").all()}

    subs = {s.assignment_id: s for s in
            Submission.query.filter_by(student_id=current_user.id)}
    assignments = (Assignment.query.filter_by(branch=branch)
                   .order_by(Assignment.due_date).all())
    pending, completed = [], []
    for a in assignments:
        if a.id in subs:
            completed.append({"a": a, "sub": subs[a.id]})
        else:
            text, overdue = time_left(a.due_date)
            pending.append({"a": a, "left": text, "overdue": overdue})

    materials = (Material.query.filter_by(branch=branch)
                 .order_by(Material.uploaded_at.desc()).all())

    # Home feed: announcements first, then upcoming calendar events.
    feed = []
    for a in (Announcement.query.filter(Announcement.branch.in_(["ALL", branch]))
              .order_by(Announcement.created_at.desc()).limit(15).all()):
        feed.append({"icon": "📢", "title": a.title, "detail": a.body or "",
                     "when": a.created_at.strftime("%d %b"),
                     "new": (now - a.created_at) < timedelta(days=3)})
    upcoming = [e for e in events if event_end(e) >= today]
    for e in upcoming:
        if (e.date - today).days <= 45:
            r = event_row(e, today, now)
            feed.append({"icon": r["icon"], "title": f"{e.title} ({r['range']})",
                         "detail": r["detail"], "when": r["when"], "new": r["new"]})
    calendar_rows = [event_row(e, today, now) for e in upcoming]
    att = get_student_attendance(current_user.id, branch)

    return render_template("student_dashboard.html", today=day_name,
                           today_lectures=today_lectures, week=week,
                           faculty=faculty, pending=pending, completed=completed,
                           materials=materials, feed=feed, att=att,
                           calendar_rows=calendar_rows, on_break=on_break)


@app.route("/student/assignment/<int:assignment_id>")
@role_required("student")
def submit_assignment(assignment_id):
    a = db.get_or_404(Assignment, assignment_id)
    if a.branch != current_user.branch:
        abort(403)
    closed = datetime.now() > a.due_date
    return render_template("submit_assignment.html", a=a, closed=closed)


# ---------- ROUTE: LIVE ALERTS (pop-ups) ----------
@app.route("/api/alerts")
@role_required("student")
def api_alerts():
    now = datetime.now()
    today = now.date()
    branch = current_user.branch
    events = visible_events(branch)
    alerts = []

    # 1) Lectures starting within 10 minutes (or started up to 5 minutes ago).
    #    No lecture alerts on a holiday or vacation day.
    if not is_break_today(events, today):
        faculty = {u.id: u.name for u in User.query.filter_by(role="teacher").all()}
        for l in Lecture.query.filter_by(branch=branch, day=now.strftime("%A")).all():
            try:
                hh, mm = (int(x) for x in l.start_time.split(":"))
                start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                continue
            diff = (start - now).total_seconds() / 60
            if -5 <= diff <= 10:
                if diff > 1:
                    when = f"Starts in {int(diff + 0.5)} min"
                elif diff >= -1:
                    when = "Starting now"
                else:
                    when = f"Started {int(-diff)} min ago"
                alerts.append({
                    "id": f"lec-{l.id}-{today.isoformat()}", "type": "lecture",
                    "title": f"📚 {l.subject}",
                    "lines": [when, f"Venue: {l.room or 'Not announced'}",
                              f"Faculty: {faculty.get(l.teacher_id, 'Not announced')}"],
                })

    # 2) Pending assignments due within 24 hours (extra reminder in the last hour).
    submitted = {s.assignment_id for s in Submission.query.filter_by(student_id=current_user.id)}
    for a in Assignment.query.filter_by(branch=branch).all():
        if a.id in submitted:
            continue
        left = (a.due_date - now).total_seconds()
        if 0 < left <= 24 * 3600:
            tier = "1h" if left <= 3600 else "24h"
            hours, mins = int(left // 3600), int((left % 3600) // 60)
            alerts.append({
                "id": f"asg-{a.id}-{tier}", "type": "assignment",
                "title": f"📝 Assignment due: {a.title}",
                "lines": [f"Due in {hours}h {mins}m",
                          f"Deadline: {a.due_date.strftime('%d %b, %H:%M')}"],
            })

    # 3) Academic calendar: exams within 7 days, other events within 3 days.
    for e in events:
        days = (e.date - today).days
        limit = 7 if e.kind == "exam" else 3
        if 0 <= days <= limit:
            when = "Today" if days == 0 else ("Tomorrow" if days == 1 else f"In {days} days")
            lines = [f"{when}: {event_range(e)}"]
            if e.description:
                lines.append(e.description[:120])
            alerts.append({
                "id": f"evt-{e.id}-{days}", "type": "calendar",
                "title": f"{KIND_ICON.get(e.kind, '📌')} {e.title}",
                "lines": lines,
            })

    resp = jsonify({"alerts": alerts})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/sw.js")
def service_worker():
    """Tiny service worker: lets phones show system notifications. No push."""
    js = ("self.addEventListener('install',()=>self.skipWaiting());"
          "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
          "self.addEventListener('notificationclick',e=>{e.notification.close();"
          "e.waitUntil(clients.openWindow('/'));});")
    return Response(js, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# ---------- ERRORS ----------
@app.route("/teacher/assignment/<int:assignment_id>/update_due", methods=["POST"])
@role_required("teacher")
def update_assignment_due(assignment_id):
    a = db.get_or_404(Assignment, assignment_id)
    if a.teacher_id != current_user.id or a.branch not in teacher_branches():
        abort(403)
    due_text = request.form.get("due_date", "")
    if due_text:
        a.due_date = datetime.strptime(due_text, "%Y-%m-%dT%H:%M")
        db.session.commit()
        flash("Due date updated.", "success")
    return redirect(url_for("teacher_dashboard"))


@app.route("/teacher/assignment/<int:assignment_id>/count/<action>", methods=["POST"])
@role_required("teacher")
def update_submission_count(assignment_id, action):
    a = db.get_or_404(Assignment, assignment_id)
    if a.teacher_id != current_user.id or a.branch not in teacher_branches():
        abort(403)
    total = User.query.filter_by(role="student", branch=a.branch).count()
    if action == "inc" and a.submitted_count < total:
        a.submitted_count += 1
    elif action == "dec" and a.submitted_count > 0:
        a.submitted_count -= 1
    db.session.commit()
    return redirect(url_for("teacher_dashboard"))

def parse_calendar_pdf(file_stream):
    reader = PdfReader(file_stream)
    events = []
    # Matches dates like: 15/08/2026, 15-08-2026, 15 Aug 2026, Aug 15, 2026
    date_pattern = re.compile(
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    )
    formats = ["%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"]

    for page in reader.pages:
        text_content = page.extract_text() or ""
        for line in text_content.splitlines():
            line = line.strip()
            match = date_pattern.search(line)
            if not match:
                continue
            raw_date = match.group(1)
            parsed_date = None
            for fmt in formats:
                try:
                    parsed_date = datetime.strptime(raw_date.replace(",", ""), fmt.replace(",", "")).date()
                    break
                except ValueError:
                    continue
            if not parsed_date:
                continue

            title = line.replace(raw_date, "").strip(" :-–—|")
            if len(title) < 3:
                continue

            lower = title.lower()
            if any(w in lower for w in ["holiday", "vacation", "break", "diwali", "holi", "eid", "christmas", "republic", "independence", "jayanti"]):
                event_type = "holiday"
            elif any(w in lower for w in ["exam", "test", "midterm", "endsem", "viva", "practical", "assessment"]):
                event_type = "exam"
            else:
                event_type = "event"

            events.append((parsed_date, title[:120], event_type))
    return events


@app.route("/teacher/calendar/auto-upload", methods=["POST"])
@role_required("teacher")
def auto_upload_calendar():
    f = request.files.get("pdf_file")
    branch = request.form.get("branch", "ALL")
    if not f or not f.filename.lower().endswith(".pdf"):
        flash("Please upload a valid PDF file.", "danger")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    extracted = parse_calendar_pdf(f.stream)
    added = 0
    for ev_date, title, ev_type in extracted:
        exists = CalendarEvent.query.filter_by(event_date=ev_date, title=title, branch=branch).first()
        if not exists:
            db.session.add(CalendarEvent(
                event_date=ev_date,
                title=title,
                event_type=ev_type,
                branch=branch,
                teacher_id=current_user.id
            ))
            added += 1
    db.session.commit()
    flash(f"Academic calendar scanned: {added} new event(s) added automatically.", "success")
    return redirect(request.referrer or url_for("teacher_dashboard"))

@app.route("/teacher/attendance", methods=["GET", "POST"])
@role_required("teacher")
def take_attendance():
    branches = teacher_branches()
    today_date = date.today()
    today_day = datetime.now().strftime("%A")

    # Auto-detect today's scheduled lectures for daily updates
    today_lectures = Lecture.query.filter(
        Lecture.branch.in_(branches), Lecture.day == today_day
    ).order_by(Lecture.start_time).all()

    selected_branch = request.values.get("branch") or (branches[0] if branches else "")
    selected_subject = request.values.get("subject") or (today_lectures[0].subject if today_lectures else "")
    selected_date_str = request.values.get("att_date") or today_date.strftime("%Y-%m-%d")
    selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()

    if selected_branch and selected_branch not in branches:
        abort(403)

    all_subjects = sorted({
        l.subject for l in Lecture.query.filter_by(branch=selected_branch).all()
    })
    students = User.query.filter_by(role="student", branch=selected_branch).order_by(User.roll_no, User.erp_id).all()

    if request.method == "POST":
        if not selected_subject:
            flash("Please select or enter a subject.", "danger")
            return redirect(url_for("take_attendance", branch=selected_branch))

        present_ids = {int(x) for x in request.form.getlist("present_ids")}
        for s in students:
            status = "Present" if s.id in present_ids else "Absent"
            existing = Attendance.query.filter_by(
                student_id=s.id, subject=selected_subject, att_date=selected_date
            ).first()
            if existing:
                existing.status = status
            else:
                db.session.add(Attendance(
                    student_id=s.id, branch=selected_branch, subject=selected_subject,
                    att_date=selected_date, status=status, teacher_id=current_user.id
                ))
        db.session.commit()
        flash(f"Attendance saved for {selected_subject} ({selected_branch}) on {selected_date_str}.", "success")
        return redirect(url_for("teacher_dashboard"))

    # Pre-load existing marks if already taken today
    existing_map = {
        a.student_id: a.status for a in Attendance.query.filter_by(
            branch=selected_branch, subject=selected_subject, att_date=selected_date
        ).all()
    }
    return render_template(
        "teacher_attendance.html",
        branches=branches, selected_branch=selected_branch,
        selected_subject=selected_subject, selected_date=selected_date_str,
        today_lectures=today_lectures, all_subjects=all_subjects,
        students=students, existing_map=existing_map
    )


@app.route("/teacher/students/import", methods=["POST"])
@role_required("teacher")
def import_students_csv():
    f = request.files.get("csv_file")
    if not f or not f.filename.lower().endswith(".csv"):
        flash("Please upload a valid CSV file.", "danger")
        return redirect(url_for("teacher_dashboard"))

    stream = io.StringIO(f.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    added = 0
    for row in reader:
        roll_no = (row.get("roll_no") or "").strip()
        erp_id = (row.get("erp_id") or roll_no).strip()
        name = (row.get("name") or "").strip()
        branch = (row.get("branch") or "").strip().upper()
        password = (row.get("password") or roll_no or "123456").strip()

        if not (erp_id and name and branch) or branch not in teacher_branches():
            continue
        if User.query.filter_by(erp_id=erp_id).first():
            continue

        db.session.add(User(
            erp_id=erp_id, roll_no=roll_no or erp_id, name=name,
            role="student", branch=branch,
            password=generate_password_hash(password)
        ))
        added += 1

    db.session.commit()
    flash(f"{added} student(s) imported and registered successfully.", "success")
    return redirect(url_for("teacher_dashboard"))

@app.errorhandler(403)
def forbidden(e):
    return "<h3>403 - You are not allowed to open this page.</h3>", 403


@app.errorhandler(413)
def too_large(e):
    flash("File is too large (maximum 10 MB).", "danger")
    return redirect(request.referrer or url_for("home"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Safely add submitted_count if it doesn't exist yet
        user_cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(user)"))]
        if "roll_no" not in user_cols:
            db.session.execute(text("ALTER TABLE user ADD COLUMN roll_no VARCHAR(30)"))
            db.session.commit()
        cols = [row[1] for row in db.session.execute(text("PRAGMA table_info(assignment)"))]
        if "submitted_count" not in cols:
            db.session.execute(text("ALTER TABLE assignment ADD COLUMN submitted_count INTEGER NOT NULL DEFAULT 0"))
            db.session.commit()
    app.run(debug=False)
