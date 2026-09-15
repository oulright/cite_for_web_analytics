"""Обучающий портал «Курсоград» — Flask + SQLite."""
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

from seed import SEED_COURSES, SEED_LESSONS, SEED_TEACHERS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "app.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")


# ---------------------------------------------------------------- база данных
def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT DEFAULT '',
    city          TEXT DEFAULT '',
    goal          TEXT DEFAULT '',
    about         TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teachers (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    role   TEXT NOT NULL,
    bio    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    level       TEXT NOT NULL,
    hours       INTEGER NOT NULL,
    teacher_id  INTEGER REFERENCES teachers(id),
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS lessons (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    title     TEXT NOT NULL,
    content   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS enrollments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, course_id)
);

CREATE TABLE IF NOT EXISTS progress (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    done_at   TEXT NOT NULL,
    UNIQUE(user_id, lesson_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    topic      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Текст-заглушка, которым уроки заполнялись в первой версии портала.
# Если в существующей базе остались такие уроки — заменяем их на полноценные.
OLD_PLACEHOLDER = "Материал урока «%: краткая теория, примеры и практическое задание."


def init_db():
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) c FROM teachers").fetchone()["c"] == 0:
            db.executemany("INSERT INTO teachers (name, role, bio) VALUES (?,?,?)",
                           SEED_TEACHERS)
        if db.execute("SELECT COUNT(*) c FROM courses").fetchone()["c"] == 0:
            db.executemany(
                "INSERT INTO courses (title, category, level, hours, teacher_id,"
                " description) VALUES (?,?,?,?,?,?)", SEED_COURSES)
            for cid, items in SEED_LESSONS.items():
                for i, (title, content) in enumerate(items, start=1):
                    db.execute(
                        "INSERT INTO lessons (course_id, position, title, content)"
                        " VALUES (?,?,?,?)", (cid, i, title, content.strip()))
        else:
            # База создана старой версией: подтягиваем содержимое уроков,
            # у которых до сих пор стоит заглушка. Прогресс студентов не трогаем.
            for cid, items in SEED_LESSONS.items():
                for i, (title, content) in enumerate(items, start=1):
                    db.execute(
                        "UPDATE lessons SET content=? WHERE course_id=? AND position=?"
                        " AND title=? AND (content='' OR content LIKE ?)",
                        (content.strip(), cid, i, title, OLD_PLACEHOLDER))
        db.commit()


# ------------------------------------------------------- разметка уроков
_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(text):
    """Экранирует текст и превращает `код` и **выделение** в HTML."""
    html = str(escape(text))
    html = _INLINE_CODE.sub(r"<code>\1</code>", html)
    html = _INLINE_BOLD.sub(r"<strong>\1</strong>", html)
    return html


@app.template_filter("lesson_html")
def lesson_html(text):
    """Переводит лёгкую разметку урока в безопасный HTML.

    Поддерживаются: абзацы (через пустую строку), ``## Подзаголовок``,
    списки ``- `` и ``1. ``, блоки кода в тройных обратных кавычках,
    а также `код` и **выделение** внутри строк.
    """
    out = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # закрывающие ```
            out.append("<pre><code>%s</code></pre>" % escape("\n".join(code)))
            continue
        if stripped.startswith("## "):
            out.append("<h2>%s</h2>" % _inline(stripped[3:]))
            i += 1
            continue
        if stripped.startswith("- "):
            items = []
            while i < n and lines[i].strip().startswith("- "):
                items.append("<li>%s</li>" % _inline(lines[i].strip()[2:]))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
            continue
        if re.match(r"^\d+\.\s", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s", lines[i].strip()):
                items.append("<li>%s</li>" % _inline(
                    re.sub(r"^\d+\.\s+", "", lines[i].strip())))
                i += 1
            out.append("<ol>%s</ol>" % "".join(items))
            continue
        # обычный абзац: собираем строки до пустой или до начала другого блока
        para = []
        while i < n:
            s = lines[i].strip()
            if (not s or s.startswith("```") or s.startswith("## ")
                    or s.startswith("- ") or re.match(r"^\d+\.\s", s)):
                break
            para.append(s)
            i += 1
        out.append("<p>%s</p>" % _inline(" ".join(para)))
    return Markup("\n".join(out))


# ------------------------------------------------------------------- хелперы
def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not session.get("user_id"):
            flash("Пожалуйста, войдите в аккаунт.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


@app.context_processor
def inject_user():
    user = None
    uid = session.get("user_id")
    if uid:
        user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return {"current_user": user, "year_now": datetime.now().year}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def course_progress(db, user_id, course_id):
    total = db.execute("SELECT COUNT(*) c FROM lessons WHERE course_id=?",
                       (course_id,)).fetchone()["c"]
    done = db.execute(
        "SELECT COUNT(*) c FROM progress p JOIN lessons l ON l.id=p.lesson_id "
        "WHERE p.user_id=? AND l.course_id=?", (user_id, course_id)).fetchone()["c"]
    pct = round(done / total * 100) if total else 0
    return {"total": total, "done": done, "pct": pct}


# ------------------------------------------------------------------ страницы
@app.route("/")
def index():
    db = get_db()
    stats = {
        "students": db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "courses": db.execute("SELECT COUNT(*) c FROM courses").fetchone()["c"],
        "lessons": db.execute("SELECT COUNT(*) c FROM lessons").fetchone()["c"],
        "teachers": db.execute("SELECT COUNT(*) c FROM teachers").fetchone()["c"],
    }
    popular = db.execute(
        "SELECT c.*, t.name teacher, "
        "(SELECT COUNT(*) FROM enrollments e WHERE e.course_id=c.id) students "
        "FROM courses c LEFT JOIN teachers t ON t.id=c.teacher_id "
        "ORDER BY students DESC, c.id LIMIT 3").fetchall()
    reviews = db.execute(
        "SELECT f.*, u.username, c.title FROM feedback f "
        "LEFT JOIN users u ON u.id=f.user_id JOIN courses c ON c.id=f.course_id "
        "ORDER BY f.id DESC LIMIT 3").fetchall()
    return render_template("index.html", stats=stats, popular=popular,
                           reviews=reviews)


@app.route("/courses")
def courses():
    db = get_db()
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    level = request.args.get("level", "")
    max_hours = request.args.get("max_hours", "").strip()
    sort = request.args.get("sort", "title")

    sql = ("SELECT c.*, t.name teacher, "
           "(SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id) lessons_n "
           "FROM courses c LEFT JOIN teachers t ON t.id=c.teacher_id WHERE 1=1")
    params = []
    if q:
        sql += " AND (c.title LIKE ? OR c.description LIKE ? OR t.name LIKE ?)"
        params += [f"%{q}%"] * 3
    if category:
        sql += " AND c.category=?"
        params.append(category)
    if level:
        sql += " AND c.level=?"
        params.append(level)
    if max_hours.isdigit():
        sql += " AND c.hours <= ?"
        params.append(int(max_hours))
    sql += " ORDER BY " + {"title": "c.title", "hours": "c.hours",
                           "hours_desc": "c.hours DESC"}.get(sort, "c.title")

    items = db.execute(sql, params).fetchall()
    categories = [r["category"] for r in
                  db.execute("SELECT DISTINCT category FROM courses ORDER BY category")]
    levels = ["Начальный", "Средний", "Продвинутый"]
    return render_template("courses.html", courses=items, categories=categories,
                           levels=levels, q=q, category=category, level=level,
                           max_hours=max_hours, sort=sort)


@app.route("/course/<int:course_id>", methods=["GET", "POST"])
def course(course_id):
    db = get_db()
    c = db.execute(
        "SELECT c.*, t.name teacher, t.role teacher_role, t.id tid "
        "FROM courses c LEFT JOIN teachers t ON t.id=c.teacher_id WHERE c.id=?",
        (course_id,)).fetchone()
    if c is None:
        return render_template("404.html"), 404

    uid = session.get("user_id")
    if request.method == "POST":
        if not uid:
            flash("Чтобы оставить отзыв, войдите в аккаунт.", "warning")
            return redirect(url_for("login", next=request.path))
        rating = request.form.get("rating", "")
        text = request.form.get("text", "").strip()
        if not rating.isdigit() or not 1 <= int(rating) <= 5:
            flash("Оценка должна быть от 1 до 5.", "error")
        elif len(text) < 10:
            flash("Отзыв должен содержать минимум 10 символов.", "error")
        else:
            db.execute("INSERT INTO feedback (user_id, course_id, rating, text,"
                       " created_at) VALUES (?,?,?,?,?)",
                       (uid, course_id, int(rating), text, now()))
            db.commit()
            flash("Спасибо за отзыв о курсе!", "success")
            return redirect(url_for("course", course_id=course_id))

    lessons = db.execute(
        "SELECT * FROM lessons WHERE course_id=? ORDER BY position",
        (course_id,)).fetchall()
    reviews = db.execute(
        "SELECT f.*, u.username FROM feedback f LEFT JOIN users u ON u.id=f.user_id"
        " WHERE f.course_id=? ORDER BY f.id DESC", (course_id,)).fetchall()
    avg = round(sum(r["rating"] for r in reviews) / len(reviews), 1) if reviews else None
    enrolled = bool(uid and db.execute(
        "SELECT 1 FROM enrollments WHERE user_id=? AND course_id=?",
        (uid, course_id)).fetchone())
    done_ids = set()
    prog = None
    if uid:
        done_ids = {r["lesson_id"] for r in db.execute(
            "SELECT p.lesson_id FROM progress p JOIN lessons l ON l.id=p.lesson_id"
            " WHERE p.user_id=? AND l.course_id=?", (uid, course_id))}
        prog = course_progress(db, uid, course_id)
    students = db.execute("SELECT COUNT(*) c FROM enrollments WHERE course_id=?",
                          (course_id,)).fetchone()["c"]
    return render_template("course.html", course=c, lessons=lessons,
                           reviews=reviews, avg=avg, enrolled=enrolled,
                           done_ids=done_ids, prog=prog, students=students)


@app.route("/course/<int:course_id>/enroll", methods=["POST"])
@login_required
def enroll(course_id):
    db = get_db()
    if db.execute("SELECT 1 FROM courses WHERE id=?", (course_id,)).fetchone() is None:
        return render_template("404.html"), 404
    db.execute("INSERT OR IGNORE INTO enrollments (user_id, course_id, created_at)"
               " VALUES (?,?,?)", (session["user_id"], course_id, now()))
    db.commit()
    flash("Вы записаны на курс. Материалы доступны в личном кабинете.", "success")
    return redirect(url_for("course", course_id=course_id))


@app.route("/course/<int:course_id>/leave", methods=["POST"])
@login_required
def leave(course_id):
    db = get_db()
    db.execute("DELETE FROM enrollments WHERE user_id=? AND course_id=?",
               (session["user_id"], course_id))
    db.commit()
    flash("Вы отписались от курса. Прогресс сохранён.", "success")
    return redirect(url_for("course", course_id=course_id))


@app.route("/lesson/<int:lesson_id>", methods=["GET", "POST"])
@login_required
def lesson(lesson_id):
    db = get_db()
    uid = session["user_id"]
    l = db.execute(
        "SELECT l.*, c.title course_title FROM lessons l "
        "JOIN courses c ON c.id=l.course_id WHERE l.id=?", (lesson_id,)).fetchone()
    if l is None:
        return render_template("404.html"), 404

    if request.method == "POST":
        if request.form.get("action") == "undo":
            db.execute("DELETE FROM progress WHERE user_id=? AND lesson_id=?",
                       (uid, lesson_id))
            flash("Отметка о прохождении снята.", "success")
        else:
            db.execute("INSERT OR IGNORE INTO progress (user_id, lesson_id, done_at)"
                       " VALUES (?,?,?)", (uid, lesson_id, now()))
            flash("Урок отмечен как пройденный.", "success")
        db.commit()
        return redirect(url_for("lesson", lesson_id=lesson_id))

    done = bool(db.execute("SELECT 1 FROM progress WHERE user_id=? AND lesson_id=?",
                           (uid, lesson_id)).fetchone())
    siblings = db.execute(
        "SELECT * FROM lessons WHERE course_id=? ORDER BY position",
        (l["course_id"],)).fetchall()
    idx = [s["id"] for s in siblings].index(lesson_id)
    prev_l = siblings[idx - 1] if idx > 0 else None
    next_l = siblings[idx + 1] if idx < len(siblings) - 1 else None
    prog = course_progress(db, uid, l["course_id"])
    done_ids = {r["lesson_id"] for r in db.execute(
        "SELECT p.lesson_id FROM progress p JOIN lessons l ON l.id=p.lesson_id"
        " WHERE p.user_id=? AND l.course_id=?", (uid, l["course_id"]))}
    # ориентировочное время чтения: ~1000 знаков в минуту, но не меньше 3 минут
    read_minutes = max(3, round(len(l["content"] or "") / 1000))
    return render_template("lesson.html", lesson=l, done=done, prev_l=prev_l,
                           next_l=next_l, prog=prog, siblings=siblings,
                           done_ids=done_ids, read_minutes=read_minutes)


@app.route("/teachers")
def teachers():
    db = get_db()
    rows = db.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM courses c WHERE c.teacher_id=t.id) n "
        "FROM teachers t ORDER BY t.name").fetchall()
    by_teacher = {t["id"]: db.execute(
        "SELECT id, title FROM courses WHERE teacher_id=? ORDER BY title",
        (t["id"],)).fetchall() for t in rows}
    return render_template("teachers.html", teachers=rows, by_teacher=by_teacher)


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    form = {"username": "", "email": "", "full_name": "", "city": "", "goal": ""}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        errors = []
        if len(form["username"]) < 3:
            errors.append("Логин должен содержать минимум 3 символа.")
        if "@" not in form["email"] or "." not in form["email"]:
            errors.append("Введите корректный e-mail.")
        if len(password) < 6:
            errors.append("Пароль должен содержать минимум 6 символов.")
        if password != password2:
            errors.append("Пароли не совпадают.")
        if not request.form.get("agree"):
            errors.append("Нужно согласиться с правилами портала.")
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE username=?",
                      (form["username"],)).fetchone():
            errors.append("Такой логин уже занят.")
        if db.execute("SELECT 1 FROM users WHERE email=?",
                      (form["email"],)).fetchone():
            errors.append("Этот e-mail уже зарегистрирован.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            cur = db.execute(
                "INSERT INTO users (username, email, password_hash, full_name,"
                " city, goal, about, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (form["username"], form["email"], generate_password_hash(password),
                 form["full_name"], form["city"], form["goal"], "", now()))
            db.commit()
            session["user_id"] = cur.lastrowid
            flash("Регистрация завершена. Добро пожаловать в «Курсоград»!", "success")
            return redirect(url_for("dashboard"))
    goals = ["Сменить профессию", "Повысить квалификацию", "Учусь для себя",
             "Подготовка к экзамену"]
    return render_template("register.html", form=form, goals=goals)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    login_value = ""
    if request.method == "POST":
        login_value = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username=? OR email=?",
            (login_value, login_value)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session.permanent = bool(request.form.get("remember"))
            flash(f"С возвращением, {user['username']}!", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Неверный логин или пароль.", "error")
    return render_template("login.html", login_value=login_value)


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        city = request.form.get("city", "").strip()
        goal = request.form.get("goal", "").strip()
        about = request.form.get("about", "").strip()
        if len(about) > 500:
            flash("«О себе» — не более 500 символов.", "error")
        else:
            db.execute("UPDATE users SET full_name=?, city=?, goal=?, about=?"
                       " WHERE id=?", (full_name, city, goal, about, uid))
            db.commit()
            flash("Профиль обновлён.", "success")
            return redirect(url_for("dashboard"))

    rows = db.execute(
        "SELECT c.*, e.created_at enrolled_at FROM enrollments e "
        "JOIN courses c ON c.id=e.course_id WHERE e.user_id=? ORDER BY e.id DESC",
        (uid,)).fetchall()
    my_courses = [{"c": r, "p": course_progress(db, uid, r["id"])} for r in rows]
    done_total = db.execute("SELECT COUNT(*) c FROM progress WHERE user_id=?",
                            (uid,)).fetchone()["c"]
    goals = ["Сменить профессию", "Повысить квалификацию", "Учусь для себя",
             "Подготовка к экзамену"]
    return render_template("dashboard.html", my_courses=my_courses,
                           done_total=done_total, goals=goals)


CATEGORY_NOTES = {
    "Веб-разработка": "Вёрстка на HTML и CSS, JavaScript и первые интерактивные интерфейсы.",
    "Программирование": "Python с нуля: синтаксис, структуры данных, функции, файлы и модули.",
    "Аналитика": "SQL, pandas и построение отчётов и графиков по реальным данным.",
    "Дизайн": "UX/UI, композиция и типографика, прототипы в Figma, исследования пользователей.",
    "Языки": "Английский для работы в IT: документация, созвоны, переписка и код-ревью.",
}

CONTACT_TOPICS = ["Вопрос о курсе", "Проблема с аккаунтом", "Стать преподавателем",
                  "Сотрудничество"]


@app.route("/about")
def about():
    db = get_db()
    stats = {
        "students": db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "courses": db.execute("SELECT COUNT(*) c FROM courses").fetchone()["c"],
        "lessons": db.execute("SELECT COUNT(*) c FROM lessons").fetchone()["c"],
        "teachers": db.execute("SELECT COUNT(*) c FROM teachers").fetchone()["c"],
        "hours": db.execute("SELECT COALESCE(SUM(hours),0) c FROM courses").fetchone()["c"],
    }
    categories = db.execute(
        "SELECT category, COUNT(*) n FROM courses GROUP BY category ORDER BY category"
    ).fetchall()
    teachers = db.execute("SELECT * FROM teachers ORDER BY name").fetchall()
    return render_template("about.html", stats=stats, categories=categories,
                           teachers=teachers, notes=CATEGORY_NOTES)


@app.route("/contacts", methods=["GET", "POST"])
def contacts():
    form = {"name": "", "email": "", "topic": "Вопрос о курсе", "body": ""}
    # тему можно передать ссылкой, например /contacts?topic=Стать преподавателем
    if request.args.get("topic") in CONTACT_TOPICS:
        form["topic"] = request.args["topic"]
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        errors = []
        if len(form["name"]) < 2:
            errors.append("Укажите имя.")
        if "@" not in form["email"]:
            errors.append("Введите корректный e-mail.")
        if len(form["body"]) < 10:
            errors.append("Сообщение должно содержать минимум 10 символов.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db = get_db()
            db.execute("INSERT INTO messages (name, email, topic, body, created_at)"
                       " VALUES (?,?,?,?,?)",
                       (form["name"], form["email"], form["topic"], form["body"],
                        now()))
            db.commit()
            flash("Сообщение отправлено — ответим на указанную почту.", "success")
            return redirect(url_for("contacts"))
    return render_template("contacts.html", form=form, topics=CONTACT_TOPICS)


@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")


@app.route("/user-agreement")
def user_agreement():
    return render_template("user_agreement.html")


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
