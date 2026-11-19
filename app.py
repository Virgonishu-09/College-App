import os
from werkzeug.utils import secure_filename

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, abort, send_from_directory)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.config["SECRET_KEY"] = "2dcf46c78f66746d771d8e796bb84a9971151b31463a03ec2519cf6066c07248"
TEACHER_CODE = "1234"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///college2.db"
db = SQLAlchemy(app)
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024      # 10 MB max
ALLOWED_EXT = {"pdf", "doc", "docx", "txt", "png", "jpg", "jpeg", "zip"}

login_manager = LoginManager(app)
login_manager.login_view = "login"


# ---------- MODELS ----------
class User(UserMixin, db.Model):
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


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    filename = db.Column(db.String(200))
    submitted_at = db.Column(db.DateTime, default=datetime.now)
    __table_args__ = (db.UniqueConstraint("assignment_id", "student_id"),)


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


# ---------- ROUTES ----------
@app.route("/")
def home():
    if current_user.is_authenticated:
        return go_to_dashboard()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        erp_id = request.form["erp_id"].strip()
        name = request.form["name"].strip()
        password = request.form["password"]
        role = request.form["role"]
        branch = request.form["branch"].strip().upper()
        print("GOT:", repr(request.form.get("TEACHER_CODE")), "EXPECTED:", repr(TEACHER_CODE))

        if not (erp_id and name and password and branch):
            flash("Please fill every field.", "danger")
        elif role not in ("teacher", "student"):
            flash("Invalid role.", "danger")
        elif role == "teacher" and request.form.get("teacher_code", "").strip() != TEACHER_CODE:
            flash("Wrong teacher code.", "danger")
            
            flash("Invalid role.", "danger")
        elif User.query.filter_by(erp_id=erp_id).first():
            flash("This ID is already registered.", "danger")
        else:
            user = User(
                erp_id=erp_id, name=name, role=role,
                password=generate_password_hash(password),
                branch=branch if role == "student" else None,
            )
            db.session.add(user)
            db.session.flush()                      # gives user.id
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
        erp_id = request.form["erp_id"].strip()
        password = request.form["password"]
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


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def teacher_branches():
    return [tb.branch for tb in
            TeacherBranch.query.filter_by(teacher_id=current_user.id).all()
        ]


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
        done = Submission.query.filter_by(assignment_id=a.id).count()
        rows.append({"a": a, "done": done, "total": total})
    lectures = Lecture.query.filter_by(teacher_id=current_user.id).all()
    return render_template("teacher_dashboard.html", branches=branches,
                           rows=rows, lectures=lectures)


@app.route("/teacher/assignment/new", methods=["GET", "POST"])
@role_required("teacher")
def new_assignment():
    branches = teacher_branches()
    if request.method == "POST":
        title = request.form["title"].strip()
        description = request.form["description"].strip()
        branch = request.form["branch"]
        due_text = request.form["due_date"]
        if branch not in branches:          # branch guard
            abort(403)
        if not title or not due_text:
            flash("Title and due date are required.", "danger")
        else:
            due = datetime.strptime(due_text, "%Y-%m-%dT%H:%M")
            db.session.add(Assignment(
                title=title, description=description, branch=branch,
                due_date=due, teacher_id=current_user.id))
            db.session.commit()
            flash("Assignment created.", "success")
            return redirect(url_for("teacher_dashboard"))
    return render_template("new_assignment.html", branches=branches)


@app.route("/teacher/assignment/<int:assignment_id>")
@role_required("teacher")
def assignment_detail(assignment_id):
    a = db.get_or_404(Assignment, assignment_id)
    if a.teacher_id != current_user.id or a.branch not in teacher_branches():
        abort(403)                          # branch guard
    students = (User.query.filter_by(role="student", branch=a.branch)
                .order_by(User.name).all())
    subs = {s.student_id: s for s in
            Submission.query.filter_by(assignment_id=a.id)}
    rows = [{"student": s, "sub": subs.get(s.id)} for s in students]
    done = sum(1 for r in rows if r["sub"])
    return render_template("assignment_detail.html", a=a, rows=rows,
                           done=done, total=len(rows))


@app.route("/teacher/lecture/new", methods=["GET", "POST"])
@role_required("teacher")
def new_lecture():
    branches = teacher_branches()
    if request.method == "POST":
        subject = request.form["subject"].strip()
        branch = request.form["branch"]
        day = request.form["day"]
        start_time = request.form["start_time"]
        room = request.form["room"].strip()
        if branch not in branches or day not in DAYS:
            abort(403)
        if not subject or not start_time:
            flash("Subject and time are required.", "danger")
        else:
            db.session.add(Lecture(
                subject=subject, branch=branch, day=day,
                start_time=start_time, room=room,
                teacher_id=current_user.id))
            db.session.commit()
            flash("Lecture added.", "success")
            return redirect(url_for("teacher_dashboard"))
    return render_template("new_lecture.html", branches=branches, days=DAYS)



def time_left(due):
    delta = due-datetime.now()
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
    today = datetime.now().strftime("%A")

    lectures = (Lecture.query.filter_by(branch=branch)
                .order_by(Lecture.start_time).all())
    today_lectures = [l for l in lectures if l.day == today]
    week = {d: [l for l in lectures if l.day == d] for d in DAYS}

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

    return render_template("student_dashboard.html", today=today,
                           today_lectures=today_lectures, week=week,
                           pending=pending, completed=completed)


@app.route("/student/assignment/<int:assignment_id>", methods=["GET", "POST"])
@role_required("student")
def submit_assignment(assignment_id):
    a = db.get_or_404(Assignment, assignment_id)
    if a.branch != current_user.branch:             # branch guard
        abort(403)

    existing = Submission.query.filter_by(
        assignment_id=a.id, student_id=current_user.id).first()
    closed = datetime.now() > a.due_date

    if request.method == "POST":
        f = request.files.get("file")
        if existing:
            flash("You have already submitted this assignment.", "warning")
        elif closed:
            flash("The due date has passed. Submission is closed.", "danger")
        elif not f or f.filename == "":
            flash("Please choose a file.", "danger")
        else:
            ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
            if ext not in ALLOWED_EXT:
                flash("File type not allowed. Use: " + ", ".join(sorted(ALLOWED_EXT)), "danger")
            else:
                name = f"{a.id}_{current_user.id}_{secure_filename(f.filename)}"
              os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=true)
               f.save(os.path.join(app.config["UPLOAD_FOLDER"], name))
                db.session.add(Submission(assignment_id=a.id,
                                          student_id=current_user.id,
                                          filename=name))
                db.session.commit()
                flash("Submitted successfully!", "success")
                return redirect(url_for("student_dashboard"))

    return render_template("submit_assignment.html", a=a,
                           existing=existing, closed=closed)
  @app.route("/uploads/<path: filename>")
@login_required 
def uploaded_file(filename):
  return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=true)
  

@app.errorhandler(403)
def forbidden(e):
    return "<h3>403 - You are not allowed to open this page.</h3>", 403
if __name__=="__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
