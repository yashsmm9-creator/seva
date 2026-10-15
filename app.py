import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'harmonycircle.db'
UPLOAD_DIR = BASE_DIR / 'static' / 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('HARMONYCIRCLE_SECRET', 'change-this-secret-before-deployment')
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
app.config['UPLOAD_FOLDER'] = str(UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      username TEXT UNIQUE,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS problems (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      category TEXT NOT NULL,
      problem_type TEXT NOT NULL CHECK(problem_type IN ('event','personal')),
      description TEXT NOT NULL,
      college_or_community TEXT NOT NULL,
      college_or_community_type TEXT NOT NULL DEFAULT 'Local Community' CHECK(college_or_community_type IN ('College','Local Community')),
      place TEXT,
      event_date TEXT,
      event_time TEXT,
      status TEXT NOT NULL DEFAULT 'Open' CHECK(status IN ('Open','In Progress','Resolved')),
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS joins (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      UNIQUE(problem_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS solutions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      message TEXT,
      pdf_filename TEXT,
      created_at TEXT NOT NULL,
      CHECK ((message IS NOT NULL AND length(trim(message)) > 0) OR pdf_filename IS NOT NULL)
    );
    CREATE TABLE IF NOT EXISTS notifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
      problem_id INTEGER REFERENCES problems(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      message TEXT NOT NULL,
      solution_id INTEGER REFERENCES solutions(id) ON DELETE CASCADE,
      is_read INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_problems_created ON problems(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_id, is_read, created_at DESC);
    ''')
    # Lightweight migrations for databases created by an earlier version.
    user_cols = {row[1] for row in db.execute('PRAGMA table_info(users)').fetchall()}
    if 'username' not in user_cols:
        db.execute('ALTER TABLE users ADD COLUMN username TEXT')
        rows = db.execute('SELECT id, name FROM users WHERE username IS NULL').fetchall()
        for row in rows:
            base = ''.join(ch.lower() for ch in row[1] if ch.isalnum()) or 'member'
            db.execute('UPDATE users SET username=? WHERE id=?', (f'{base}{row[0]}', row[0]))
    problem_cols = {row[1] for row in db.execute('PRAGMA table_info(problems)').fetchall()}
    if 'status' not in problem_cols:
        db.execute("ALTER TABLE problems ADD COLUMN status TEXT NOT NULL DEFAULT 'Open'")
    if 'college_or_community_type' not in problem_cols:
        db.execute("ALTER TABLE problems ADD COLUMN college_or_community_type TEXT NOT NULL DEFAULT 'Local Community'")
    db.commit()
    db.close()


def now():
    return datetime.now().isoformat(timespec='seconds')


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(24)
    return session['csrf_token']

app.jinja_env.globals['csrf_token'] = csrf_token


@app.before_request
def protect_posts():
    if request.method == 'POST':
        submitted = request.form.get('_csrf_token', '')
        expected = session.get('csrf_token', '')
        if not submitted or not expected or not secrets.compare_digest(submitted, expected):
            abort(400, description='Your form session expired. Please go back, refresh the page, and try again.')


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            flash('Please log in or create an account to continue.', 'info')
            return redirect(url_for('login', next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return get_db().execute('SELECT id, name, email FROM users WHERE id = ?', (user_id,)).fetchone()

app.jinja_env.globals['current_user'] = current_user


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    db = get_db()
    search = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()
    kind = request.args.get('type', '').strip()
    scope_filter = request.args.get('scope', '').strip()
    status_filter = request.args.get('status', '').strip()
    sql = '''SELECT p.*, u.name AS author,
             (SELECT COUNT(*) FROM joins j WHERE j.problem_id=p.id) AS join_count,
             (SELECT COUNT(*) FROM solutions s WHERE s.problem_id=p.id) AS solution_count
             FROM problems p JOIN users u ON u.id=p.user_id WHERE 1=1'''
    params = []
    if search:
        sql += ' AND (p.title LIKE ? OR p.description LIKE ? OR p.college_or_community LIKE ? OR p.place LIKE ?)'
        term = f'%{search}%'
        params.extend([term, term, term, term])
    if category in {'Environment', 'Education', 'Disaster relief', 'Accessibility', 'Cleanliness', 'Wellbeing', 'Other'}:
        sql += ' AND p.category=?'; params.append(category)
    if kind in {'event', 'personal'}:
        sql += ' AND p.problem_type=?'; params.append(kind)
    if scope_filter in {'College', 'Local Community'}:
        sql += ' AND p.college_or_community_type=?'; params.append(scope_filter)
    if status_filter in {'Open', 'In Progress', 'Resolved'}:
        sql += ' AND p.status=?'; params.append(status_filter)
    sql += ' ORDER BY CASE WHEN p.problem_type="event" THEN 0 ELSE 1 END, p.event_date ASC, p.created_at DESC'
    problems = db.execute(sql, params).fetchall()
    counts = {
      'problems': db.execute('SELECT COUNT(*) FROM problems').fetchone()[0],
      'events': db.execute("SELECT COUNT(*) FROM problems WHERE problem_type='event'").fetchone()[0],
      'members': db.execute('SELECT COUNT(*) FROM users').fetchone()[0],
      'solutions': db.execute('SELECT COUNT(*) FROM solutions').fetchone()[0],
    }
    return render_template('index.html', problems=problems, counts=counts, search=search, category=category, kind=kind, scope_filter=scope_filter, status_filter=status_filter)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        username = request.form.get('username', '').strip().lower()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('auth.html', mode='register')
        if get_db().execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            flash('That username is already in use. Choose another.', 'error')
            return render_template('auth.html', mode='register')
        if len(name) < 2 or len(name) > 80 or not (3 <= len(username) <= 24) or not username.replace('_','').isalnum() or '@' not in email or len(password) < 8:
            flash('Enter your name, a valid email and username (3–24 letters, numbers or underscores). Password must be at least 8 characters.', 'error')
            return render_template('auth.html', mode='register')
        try:
            db = get_db()
            cur = db.execute('INSERT INTO users(name,username,email,password_hash,created_at) VALUES(?,?,?,?,?)',
                             (name, username, email, generate_password_hash(password), now()))
            db.commit()
            session.clear(); session['user_id'] = cur.lastrowid; session['csrf_token'] = secrets.token_urlsafe(24)
            flash('Welcome to HarmonyCircle! Your account is ready.', 'success')
            return redirect(url_for('index'))
        except sqlite3.IntegrityError:
            flash('That email or username is already in use. Please log in or choose another.', 'error')
    return render_template('auth.html', mode='register')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = get_db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear(); session['user_id'] = user['id']; session['csrf_token'] = secrets.token_urlsafe(24)
            next_url = request.args.get('next', '')
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = url_for('index')
            return redirect(next_url)
        flash('Email or password was not correct.', 'error')
    return render_template('auth.html', mode='login')


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    flash('You have logged out.', 'info')
    return redirect(url_for('index'))


@app.route('/problems/new', methods=['GET', 'POST'])
@login_required
def new_problem():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', 'Other')
        problem_type = request.form.get('problem_type', 'personal')
        scope = request.form.get('scope', '').strip()
        scope_type = request.form.get('scope_type', 'Local Community').strip()
        place = request.form.get('place', '').strip()
        event_date = request.form.get('event_date', '').strip()
        event_time = request.form.get('event_time', '').strip()
        valid_categories = {'Environment', 'Education', 'Disaster relief', 'Accessibility', 'Cleanliness', 'Wellbeing', 'Other'}
        errors = []
        if len(title) < 5 or len(title) > 140: errors.append('Title must be between 5 and 140 characters.')
        if len(description) < 15 or len(description) > 5000: errors.append('Description must be between 15 and 5,000 characters.')
        if not scope or len(scope) > 140: errors.append('Add the college or community name/area.')
        if scope_type not in {'College', 'Local Community'}: errors.append('Choose College or Local Community.')
        if problem_type not in {'event', 'personal'}: errors.append('Choose Event or Personal problem.')
        if category not in valid_categories: category = 'Other'
        if problem_type == 'event':
            try:
                dt = datetime.strptime(event_date, '%Y-%m-%d')
                if dt.date() < datetime.now().date(): errors.append('Event date cannot be in the past.')
            except ValueError: errors.append('Choose a valid event date.')
            try: datetime.strptime(event_time, '%H:%M')
            except ValueError: errors.append('Choose a valid event time.')
            if len(place) < 2: errors.append('Event place is required.')
        else:
            place = event_date = event_time = None
        if errors:
            for e in errors: flash(e, 'error')
            return render_template('new_problem.html', form=request.form)
        db = get_db()
        cur = db.execute('''INSERT INTO problems(user_id,title,category,problem_type,description,college_or_community,college_or_community_type,place,event_date,event_time,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                         (session['user_id'], title, category, problem_type, description, scope, scope_type, place, event_date, event_time, now()))
        db.commit()
        flash('Your problem has been posted to the community.', 'success')
        return redirect(url_for('problem_detail', problem_id=cur.lastrowid))
    return render_template('new_problem.html', form={})


@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    uid = session['user_id']
    my_posts = db.execute("SELECT p.*, (SELECT COUNT(*) FROM joins j WHERE j.problem_id=p.id) AS join_count FROM problems p WHERE p.user_id=? ORDER BY p.created_at DESC", (uid,)).fetchall()
    joined_posts = db.execute("SELECT p.*, u.name AS author FROM joins j JOIN problems p ON p.id=j.problem_id JOIN users u ON u.id=p.user_id WHERE j.user_id=? ORDER BY j.created_at DESC", (uid,)).fetchall()
    my_solutions = db.execute("SELECT s.*, p.title, p.id AS problem_id FROM solutions s JOIN problems p ON p.id=s.problem_id WHERE s.user_id=? ORDER BY s.created_at DESC", (uid,)).fetchall()
    return render_template('dashboard.html', my_posts=my_posts, joined_posts=joined_posts, my_solutions=my_solutions)


@app.route('/problems/<int:problem_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_problem(problem_id):
    db = get_db()
    problem = db.execute('SELECT * FROM problems WHERE id=?', (problem_id,)).fetchone()
    if not problem: abort(404)
    if problem['user_id'] != session['user_id']:
        abort(403)
    if request.method == 'POST':
        status = request.form.get('status', 'Open')
        if status not in {'Open', 'In Progress', 'Resolved'}:
            flash('Choose a valid status.', 'error')
        else:
            db.execute('UPDATE problems SET status=? WHERE id=?', (status, problem_id))
            db.commit()
            flash('Problem status updated.', 'success')
            return redirect(url_for('problem_detail', problem_id=problem_id))
    return render_template('edit_problem.html', problem=problem)


@app.route('/problems/<int:problem_id>')
def problem_detail(problem_id):
    db = get_db()
    problem = db.execute('''SELECT p.*,u.name AS author,u.id AS author_id,
        (SELECT COUNT(*) FROM joins j WHERE j.problem_id=p.id) AS join_count,
        (SELECT COUNT(*) FROM solutions s WHERE s.problem_id=p.id) AS solution_count
        FROM problems p JOIN users u ON u.id=p.user_id WHERE p.id=?''', (problem_id,)).fetchone()
    if not problem: abort(404)
    user_id = session.get('user_id')
    joined = bool(user_id and db.execute('SELECT 1 FROM joins WHERE problem_id=? AND user_id=?', (problem_id,user_id)).fetchone())
    solutions = db.execute('''SELECT s.*,u.name AS author FROM solutions s JOIN users u ON u.id=s.user_id
                              WHERE s.problem_id=? ORDER BY s.created_at DESC''', (problem_id,)).fetchall()
    return render_template('detail.html', problem=problem, joined=joined, solutions=solutions)


@app.route('/problems/<int:problem_id>/join', methods=['POST'])
@login_required
def join_problem(problem_id):
    db = get_db()
    problem = db.execute('SELECT * FROM problems WHERE id=?', (problem_id,)).fetchone()
    if not problem: abort(404)
    if problem['problem_type'] != 'event':
        flash('Joining is available for events. You can send a solution or offer help on a personal problem.', 'info')
        return redirect(url_for('problem_detail', problem_id=problem_id))
    if problem['user_id'] == session['user_id']:
        flash('You created this event, so you are already its organiser.', 'info')
    else:
        existing = db.execute('SELECT id FROM joins WHERE problem_id=? AND user_id=?', (problem_id,session['user_id'])).fetchone()
        if existing:
            db.execute('DELETE FROM joins WHERE id=?', (existing['id'],))
            flash('You have left this campaign.', 'info')
        else:
            db.execute('INSERT INTO joins(problem_id,user_id,created_at) VALUES(?,?,?)', (problem_id,session['user_id'],now()))
            actor = current_user()
            db.execute('INSERT INTO notifications(recipient_id,actor_id,problem_id,kind,message,created_at) VALUES(?,?,?,?,?,?)',
                       (problem['user_id'], session['user_id'], problem_id, 'join', f'{actor["name"]} joined your campaign: {problem["title"]}', now()))
            flash('You joined the campaign. The organiser has been notified.', 'success')
        db.commit()
    return redirect(url_for('problem_detail', problem_id=problem_id))


@app.route('/problems/<int:problem_id>/solutions', methods=['POST'])
@login_required
def add_solution(problem_id):
    db = get_db()
    problem = db.execute('SELECT * FROM problems WHERE id=?', (problem_id,)).fetchone()
    if not problem: abort(404)
    message = request.form.get('message', '').strip()
    uploaded = request.files.get('pdf')
    filename = None
    if uploaded and uploaded.filename:
        if not allowed_file(uploaded.filename):
            flash('Only PDF files are allowed.', 'error')
            return redirect(url_for('problem_detail', problem_id=problem_id) + '#solution-form')
        # Check the PDF signature as well as the extension.
        signature = uploaded.stream.read(5)
        uploaded.stream.seek(0)
        if signature != b'%PDF-':
            flash('The uploaded file does not appear to be a valid PDF.', 'error')
            return redirect(url_for('problem_detail', problem_id=problem_id) + '#solution-form')
        # Save with an unguessable name to avoid collisions and path issues.
        filename = secrets.token_hex(12) + '.pdf'
        uploaded.save(UPLOAD_DIR / filename)
    if not message and not filename:
        flash('Write a solution or attach a PDF (or both).', 'error')
        return redirect(url_for('problem_detail', problem_id=problem_id) + '#solution-form')
    if len(message) > 5000:
        if filename: (UPLOAD_DIR / filename).unlink(missing_ok=True)
        flash('Text solution must be 5,000 characters or fewer.', 'error')
        return redirect(url_for('problem_detail', problem_id=problem_id) + '#solution-form')
    if problem['user_id'] == session['user_id']:
        if filename: (UPLOAD_DIR / filename).unlink(missing_ok=True)
        flash('You cannot send a solution to your own post.', 'info')
        return redirect(url_for('problem_detail', problem_id=problem_id))
    cur = db.execute('INSERT INTO solutions(problem_id,user_id,message,pdf_filename,created_at) VALUES(?,?,?,?,?)',
                     (problem_id, session['user_id'], message or None, filename, now()))
    actor = current_user()
    db.execute('INSERT INTO notifications(recipient_id,actor_id,problem_id,kind,message,solution_id,created_at) VALUES(?,?,?,?,?,?,?)',
               (problem['user_id'], session['user_id'], problem_id, 'solution', f'{actor["name"]} offered help on: {problem["title"]}', cur.lastrowid, now()))
    db.commit()
    flash('Your help was sent. The person who posted the problem has been notified.', 'success')
    return redirect(url_for('problem_detail', problem_id=problem_id))


@app.route('/uploads/<filename>')
@login_required
def download_upload(filename):
    # Only allow files referenced by a solution the current user is allowed to view.
    db = get_db()
    row = db.execute('''SELECT s.id,p.user_id,s.user_id AS solver_id FROM solutions s JOIN problems p ON p.id=s.problem_id
                        WHERE s.pdf_filename=?''', (filename,)).fetchone()
    if not row: abort(404)
    if session['user_id'] not in (row['user_id'], row['solver_id']): abort(403)
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True, download_name='community-solution.pdf')


@app.route('/notifications')
@login_required
def notifications():
    db = get_db()
    items = db.execute('''SELECT n.*,p.title AS problem_title FROM notifications n
                          LEFT JOIN problems p ON p.id=n.problem_id WHERE n.recipient_id=?
                          ORDER BY n.created_at DESC LIMIT 100''', (session['user_id'],)).fetchall()
    db.execute('UPDATE notifications SET is_read=1 WHERE recipient_id=?', (session['user_id'],)); db.commit()
    return render_template('notifications.html', items=items)


@app.context_processor
def inject_notification_count():
    if session.get('user_id'):
        count = get_db().execute('SELECT COUNT(*) FROM notifications WHERE recipient_id=? AND is_read=0', (session['user_id'],)).fetchone()[0]
    else:
        count = 0
    return {'notification_count': count}


@app.errorhandler(413)
def too_large(_error):
    flash('The uploaded file is too large. Maximum PDF size is 8 MB.', 'error')
    return redirect(request.referrer or url_for('index'))


@app.errorhandler(400)
def bad_request(error):
    return render_template('error.html', code=400, message=getattr(error, 'description', 'Bad request.')), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template('error.html', code=403, message='You do not have permission to view this file.'), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template('error.html', code=404, message='We could not find that page.'), 404


init_db()
if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG', '0') == '1')
