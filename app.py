from functools import wraps
from flask import Flask, render_template, jsonify, request, session, url_for
from werkzeug.utils import redirect, secure_filename
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from datetime import datetime
import pdfplumber
import json
import os
import re
import requests
import uuid


def load_local_env(path='.env'):
    if not os.path.exists(path):
        return

    with open(path, 'r', encoding='utf-8') as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue

            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_local_env()

app = Flask(__name__, static_folder='Static', template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config["UPLOAD_FOLDER"] = "uploads/"
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB
app.secret_key = 'Itz_1001_Mid'  # Add secret key for sessions
db = SQLAlchemy(app)

ALLOWED_EXTENSIONS = {'pdf', 'txt'}
ROLE_CLIENT = 'client'
ROLE_ADMIN = 'admin'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class user(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(80), nullable = False, unique = True)
    email = db.Column(db.String(80), nullable = False, unique = True)
    password = db.Column(db.String(225), nullable = False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_CLIENT)

    def __repr__(self) -> str:
        return f"User {self.id} {self.username}"
    
#-------------------------File model------------------------------------------------
class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)

    original_filename = db.Column(db.String(255))

    filetype = db.Column(db.String(50))
    word_count = db.Column(db.Integer)
    content = db.Column(db.Text)

    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

#-------------------------History model------------------------------------------------
class History(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    action = db.Column(db.String(50))  # upload / delete / summarize / etc

    file_id = db.Column(db.Integer, nullable=True)
    filename = db.Column(db.String(255))
    generated_content = db.Column(db.Text)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

#-------------------------User authentication system------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Login required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user_is_admin():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Administrator access required'}), 403
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated

def configured_admin_emails():
    configured = os.getenv('ADMIN_EMAILS', '')
    return {
        email.strip().lower()
        for email in configured.split(',')
        if email.strip()
    }

def promote_configured_admin(user_obj):
    if user_obj and user_obj.email.lower() in configured_admin_emails() and user_obj.role != ROLE_ADMIN:
        user_obj.role = ROLE_ADMIN
        db.session.commit()
    return user_obj

def current_user():
    return get_user_by_id(session['user_id']) if 'user_id' in session else None

def current_user_is_admin():
    user_obj = promote_configured_admin(current_user())
    return bool(user_obj and user_obj.role == ROLE_ADMIN)

def can_view_owner_data(owner_id):
    return owner_id == session.get('user_id') or current_user_is_admin()

def owner_details(owner_id):
    owner = db.session.get(user, owner_id)
    if not owner:
        return {'owner_id': owner_id, 'owner_username': 'Unknown user', 'owner_email': ''}
    return {
        'owner_id': owner.id,
        'owner_username': owner.username,
        'owner_email': owner.email
    }

def create_user(username, email, password):
    try:
        hashed_password = generate_password_hash(password)
        role = ROLE_ADMIN if email.lower() in configured_admin_emails() else ROLE_CLIENT
        new_user = user(username=username, email=email, password=hashed_password, role=role)
        db.session.add(new_user)
        db.session.commit()
        return new_user.id, None
    
    except Exception as e:
        db.session.rollback()
        err = str(e).lower()
        if 'username' in err:
            return None, 'Username already taken'
        if 'email' in err:
            return None, 'Email already registered'
        return None, 'Registration failed'
    
def get_user_by_email(email):
    return user.query.filter_by(email=email).first()

def get_user_by_username(username):
    return user.query.filter_by(username=username).first()

def get_user_by_id(user_id):
    return db.session.get(user, user_id)

def validate_email(email):
    return '@' in email and '.' in email.split('@')[-1]

def validate_password(password):
    if len(password) < 8:
        return False, "Password needs at least 8 characters"
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter'
    if not re.search(r'\d', password):
        return False, 'Password must contain at least one number'
    return True, None

# --------------------------Upload file system -----------------------------------------------
def allow_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(filepath):
    if filepath.endswith('.pdf'):
        text = ''
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
        return text.strip()
    elif filepath.endswith('.txt'):
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return ''

def get_word_count(text):
    return len(text.split())

def get_preview(text, chars=300):
    return text[:chars] + '...' if len(text) > chars else text


# --------------------------Groq AI agent------------------------------------------------
GROQ_CHAT_COMPLETIONS_URL = 'https://api.groq.com/openai/v1/chat/completions'
DEFAULT_GROQ_MODEL = 'llama-3.3-70b-versatile'


def call_groq_agent(user_prompt, *, temperature=0.2, max_completion_tokens=1200):
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise RuntimeError('Missing GROQ_API_KEY environment variable')

    payload = {
        'model': os.getenv('GROQ_MODEL', DEFAULT_GROQ_MODEL),
        'messages': [
            {
                'role': 'system',
                'content': (
                    'You are StudyAI, a concise AI study agent. '
                    'Help students turn source material into accurate, clear study outputs. '
                    'Do not invent facts that are not supported by the provided document.'
                )
            },
            {'role': 'user', 'content': user_prompt}
        ],
        'temperature': temperature,
        'max_completion_tokens': max_completion_tokens
    }

    try:
        response = requests.post(
            GROQ_CHAT_COMPLETIONS_URL,
            json=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'User-Agent': 'StudyAI/1.0'
            },
            timeout=60
        )
    except requests.RequestException as e:
        raise RuntimeError(f'Could not connect to Groq: {e}') from e

    if not response.ok:
        try:
            error_data = response.json()
            detail = error_data.get('error', {}).get('message') or error_data
        except ValueError:
            detail = response.text.strip()
        raise RuntimeError(f'Groq API error ({response.status_code}): {detail}')

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError('Groq returned a non-JSON response') from e

    try:
        return data['choices'][0]['message']['content'].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError('Groq returned an unexpected response format') from e


def extract_json_array(raw):
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\[[\s\S]*\]', raw)
        if not match:
            raise
        return json.loads(match.group(0))


#--------------------------File upload API------------------------------------------------
@app.route('/upload', methods = ['POST'])
@login_required
def upload_file():
    if 'file' not in request.files and 'document' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400
    
    file = request.files.get('file') or request.files.get('document')
    if not file or file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400
    if not allow_file(file.filename):
        return jsonify({'success': False, 'error': 'File type not allowed'}), 400
    
    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename )
    file.save(filepath)

    extracted_text = extract_text(filepath)

    if not extracted_text:
        os.remove(filepath)
        return jsonify({
        'success': False,
        'error': 'Could not extract text from file'
    }), 400

    word_count = get_word_count(extracted_text)
    file_type = file.filename.rsplit('.', 1)[1].lower()

    new_file = File(
        filename=filename,
        original_filename=file.filename,
        filetype=file_type,
        word_count=word_count,
        content=extracted_text,
        user_id=session['user_id']
    )

    db.session.add(new_file)
    db.session.commit()

    history = History(
    action='upload',
    file_id=new_file.id,
    filename=file.filename,
    user_id=session['user_id']
    )
    db.session.add(history)
    db.session.commit()

    return jsonify({
    'success': True,
    'file_id': new_file.id,
    'doc_id': new_file.id,
    'filename': file.filename,
    'stored_filename': filename,
    'word_count': word_count,
    'preview': get_preview(extracted_text),
    'full_text': extracted_text
})

#--------------------------File Delete API------------------------------------------------
@app.route('/files/<int:file_id>', methods=['DELETE'])
@app.route('/documents/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file(file_id):
    user_file = db.session.get(File, file_id)

    if not user_file or user_file.user_id != session['user_id']:
        return jsonify({'error': 'File not found'}), 404

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], user_file.filename)

    if os.path.exists(filepath):
        os.remove(filepath)

    history = History(
        action='delete',
        file_id=user_file.id,
        filename=user_file.original_filename,
        user_id=session['user_id']
    )

    db.session.delete(user_file)
    db.session.add(history)
    db.session.commit()

    return jsonify({'success': True})
    
#--------------------------Document APIs------------------------------------------------
@app.route('/documents', methods=['GET'])
@login_required
def list_documents():
    query = File.query
    is_admin_view = current_user_is_admin() and request.args.get('scope') != 'own'
    if not is_admin_view:
        query = query.filter_by(user_id=session['user_id'])
    documents = query \
        .order_by(File.uploaded_at.desc()) \
        .all()

    docs = []
    for doc in documents:
        doc_data = {
            'id': doc.id,
            'filename': doc.original_filename or doc.filename,
            'stored_filename': doc.filename,
            'filetype': doc.filetype,
            'word_count': doc.word_count,
            'preview': get_preview(doc.content or ''),
            'uploaded_at': doc.uploaded_at.strftime('%Y-%m-%d %H:%M'),
            'read_only': doc.user_id != session['user_id']
        }
        if is_admin_view:
            doc_data.update(owner_details(doc.user_id))
        docs.append(doc_data)

    return jsonify({'success': True, 'documents': docs, 'admin_view': is_admin_view})

@app.route('/documents/<int:file_id>', methods=['GET'])
@login_required
def get_document(file_id):
    doc = db.session.get(File, file_id)
    if not doc or not can_view_owner_data(doc.user_id):
        return jsonify({'success': False, 'error': 'Document not found'}), 404

    result = {
        'success': True,
        'id': doc.id,
        'filename': doc.original_filename or doc.filename,
        'filetype': doc.filetype,
        'word_count': doc.word_count,
        'content': doc.content or '',
        'uploaded_at': doc.uploaded_at.strftime('%Y-%m-%d %H:%M'),
        'read_only': doc.user_id != session['user_id']
    }
    if current_user_is_admin():
        result.update(owner_details(doc.user_id))
    return jsonify(result)

#--------------------------User authentication system API------------------------------------------------
@app.route('/api/register', methods = ['POST'])
def register():
    data     = request.get_json()
    username = (data.get('username') or '').strip()
    email    = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()
    confirm  = (data.get('confirm') or '').strip()

    if not username or not email or not password:
        return jsonify({'success': False, 'error': 'All fields are required'}), 400
    if len(username) < 3:
        return jsonify({'success': False, 'error': 'Username must be at least 3 characters'}), 400
    if not validate_email(email):
        return jsonify({'success': False, 'error': 'Invalid email address'}), 400
    valid, msg = validate_password(password)
    if not valid:
        return jsonify({'success': False, 'error': msg}), 400
    if password != confirm:
        return jsonify({'success': False, 'error': 'Passwords do not match'}), 400

    user_id, error = create_user(username, email, password)
    if error:
        return jsonify({'success': False, 'error': error}), 400

    session['user_id']  = user_id
    session['username'] = username
    registered_user = get_user_by_id(user_id)
    return jsonify({'success': True, 'username': username, 'role': registered_user.role}), 201

@app.route('/api/login', methods = ['POST'])
def login_user():
    data      = request.get_json() or {}
    userobj   = (data.get('userobj') or '').strip()
    password  = (data.get('password') or '').strip()
    user_obj  = get_user_by_username(userobj) or get_user_by_email(userobj.lower())

    if not user_obj or not password:
        return jsonify({'success': False, 'error': 'Email/Username and password are required'}), 400

    if not check_password_hash(user_obj.password, password):
        return jsonify({'success': False, 'error': 'Invalid email/username or password'}), 401

    session['user_id']  = user_obj.id
    promote_configured_admin(user_obj)
    session['username'] = user_obj.username
    return jsonify({'success': True, 'username': user_obj.username, 'role': user_obj.role})

@app.route('/api/forgot_password', methods = ['POST'])
def forgot_password_api():
    data = request.get_json() or {}
    user_input = (data.get('userobj') or data.get('email') or '').strip()
    if not user_input:
        return jsonify({'success': False, 'error': 'Email or username is required'}), 400

    if '@' in user_input:
        user_obj = get_user_by_email(user_input.lower())
    else:
        user_obj = get_user_by_username(user_input)

    if not user_obj:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # In a real application, you would send an email with a reset link here
    return jsonify({'success': True, 'message': 'Password reset instructions sent to your email (not really, this is a demo)'})



@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('landing'))

@app.route('/me', methods = ['GET'])
@login_required
def me():
    user_obj = promote_configured_admin(get_user_by_id(session['user_id']))
    if not user_obj:
        session.clear()
        return jsonify({'success': False, 'error': 'User not found'}), 404
    return jsonify({
        'success': True,
        'id': user_obj.id,
        'username': user_obj.username,
        'email': user_obj.email,
        'role': user_obj.role
    })

@app.route('/me', methods=['PUT'])
@login_required
def update_me():
    current_user = get_user_by_id(session['user_id'])
    if not current_user:
        session.clear()
        return jsonify({'success': False, 'error': 'User not found'}), 404

    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    current_password = (data.get('current_password') or '').strip()
    new_password = (data.get('new_password') or '').strip()

    if not username or not email:
        return jsonify({'success': False, 'error': 'Username and email are required'}), 400
    if len(username) < 3:
        return jsonify({'success': False, 'error': 'Username must be at least 3 characters'}), 400
    if not validate_email(email):
        return jsonify({'success': False, 'error': 'Invalid email address'}), 400

    username_owner = get_user_by_username(username)
    if username_owner and username_owner.id != current_user.id:
        return jsonify({'success': False, 'error': 'Username already taken'}), 400

    email_owner = get_user_by_email(email)
    if email_owner and email_owner.id != current_user.id:
        return jsonify({'success': False, 'error': 'Email already registered'}), 400

    if new_password:
        if not current_password:
            return jsonify({'success': False, 'error': 'Current password is required to change password'}), 400
        if not check_password_hash(current_user.password, current_password):
            return jsonify({'success': False, 'error': 'Current password is incorrect'}), 401
        valid, msg = validate_password(new_password)
        if not valid:
            return jsonify({'success': False, 'error': msg}), 400
        current_user.password = generate_password_hash(new_password)

    current_user.username = username
    current_user.email = email
    db.session.commit()

    session['username'] = current_user.username
    return jsonify({
        'success': True,
        'id': current_user.id,
        'username': current_user.username,
        'email': current_user.email,
        'role': current_user.role
    })

@app.route('/api/admin/overview', methods=['GET'])
@admin_required
def admin_overview():
    accounts = user.query.order_by(user.username.asc()).all()
    users = []
    for account in accounts:
        users.append({
            'id': account.id,
            'username': account.username,
            'email': account.email,
            'role': account.role,
            'document_count': File.query.filter_by(user_id=account.id).count(),
            'history_count': History.query.filter_by(user_id=account.id).count()
        })

    return jsonify({
        'success': True,
        'users': users,
        'user_count': len(users),
        'document_count': File.query.count(),
        'history_count': History.query.count()
    })

# -------------------------AI Summarize API------------------------------------------------
@app.route('/api/summarize', methods=['POST'])
@login_required
def ai_summarize():
    data = request.get_json() or {}
    file_id = data.get('file_id') or data.get('doc_id')
    text = data.get('text', '').strip()

    # Get text from file_id if provided
    if file_id and not text:
        doc = db.session.get(File, int(file_id))
        if not doc or doc.user_id != session['user_id']:
            return jsonify({'success': False, 'error': 'Document not found'}), 404
        text = doc.content or ''
        filename = doc.original_filename or doc.filename
    else:
        filename = data.get('filename', 'document')

    if not text:
        return jsonify({'success': False, 'error': 'No text to summarize'}), 400

    # Truncate to ~12,000 words to stay within token limits
    words = text.split()
    if len(words) > 12000:
        text = ' '.join(words[:12000]) + '\n\n[Content truncated for summarization]'

    try:
        summary = call_groq_agent(f"""Please summarize the following document clearly and concisely.
Structure your summary with:
1. *Overview* (2-3 sentences on the main topic)
2. *Key Points* (bullet list of 4-6 main ideas)
3. *Conclusion* (1-2 sentences on the overall takeaway)

Document:
{text}""", temperature=0.2, max_completion_tokens=1400)

        # Log to history
        if file_id:
            history_entry = History(
                action='summarize',
                file_id=int(file_id),
                filename=filename,
                user_id=session['user_id']
            )
            db.session.add(history_entry)
            db.session.commit()

        return jsonify({'success': True, 'summary': summary, 'filename': filename})

    except Exception as e:
        return jsonify({'success': False, 'error': f'Groq summarization failed: {str(e)}'}), 500
    
# -------------------------AI Flashcards API------------------------------------------------
@app.route('/api/flashcards', methods=['POST'])
@login_required
def ai_flashcards():
    data = request.get_json() or {}
    file_id = data.get('file_id') or data.get('doc_id')
    text = data.get('text', '').strip()
    try:
        count = max(3, min(int(data.get('count', 10)), 20))  # 3-20 cards
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Card count must be a number'}), 400

    # Get text from file_id if provided
    if file_id and not text:
        doc = db.session.get(File, int(file_id))
        if not doc or doc.user_id != session['user_id']:
            return jsonify({'success': False, 'error': 'Document not found'}), 404
        text = doc.content or ''
        filename = doc.original_filename or doc.filename
    else:
        filename = data.get('filename', 'document')

    if not text:
        return jsonify({'success': False, 'error': 'No text to generate flashcards from'}), 400

    # Truncate to stay within token limits
    words = text.split()
    if len(words) > 12000:
        text = ' '.join(words[:12000]) + '\n\n[Content truncated]'

    try:
        raw = call_groq_agent(f"""Generate exactly {count} flashcards from the following document for studying.

Return ONLY a valid JSON array, no extra text, no markdown, no code blocks. Format:
[
  {{"question": "...", "answer": "..."}},
  ...
]

Rules:
- Questions should test understanding, not just recall
- Answers should be concise (1-3 sentences)
- Cover diverse topics from the document
- Use clear, simple language

Document:
{text}""", temperature=0.1, max_completion_tokens=1800)

        flashcards = extract_json_array(raw)

        if not isinstance(flashcards, list):
            raise ValueError("Response is not a list")

        # Normalize keys
        normalized = []
        for card in flashcards:
            if isinstance(card, dict):
                normalized.append({
                    'question': str(card.get('question', card.get('front', ''))),
                    'answer': str(card.get('answer', card.get('back', '')))
                })

        # Log to history
        if file_id:
            history_entry = History(
                action='flashcards',
                file_id=int(file_id),
                filename=filename,
                generated_content=json.dumps(normalized),
                user_id=session['user_id']
            )
            db.session.add(history_entry)
            db.session.commit()

        return jsonify({'success': True, 'flashcards': normalized, 'filename': filename})

    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Failed to parse flashcards: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'AI flashcard generation failed: {str(e)}'}), 500

# -------------------------AI Quiz API------------------------------------------------
@app.route('/api/quizzes', methods=['POST'])
@login_required
def ai_quizzes():
    data = request.get_json() or {}
    file_id = data.get('file_id') or data.get('doc_id')
    text = data.get('text', '').strip()
    try:
        count = max(3, min(int(data.get('count', 8)), 15))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Question count must be a number'}), 400

    if file_id and not text:
        doc = db.session.get(File, int(file_id))
        if not doc or doc.user_id != session['user_id']:
            return jsonify({'success': False, 'error': 'Document not found'}), 404
        text = doc.content or ''
        filename = doc.original_filename or doc.filename
    else:
        filename = data.get('filename', 'document')

    if not text:
        return jsonify({'success': False, 'error': 'No text to generate a quiz from'}), 400

    words = text.split()
    if len(words) > 12000:
        text = ' '.join(words[:12000]) + '\n\n[Content truncated]'

    try:
        raw = call_groq_agent(f"""Generate exactly {count} multiple-choice quiz questions from the following document.

Return ONLY a valid JSON array, no extra text, no markdown, no code blocks. Format:
[
  {{
    "question": "...",
    "options": ["...", "...", "...", "..."],
    "answer": "...",
    "explanation": "..."
  }}
]

Rules:
- Each question must have exactly 4 plausible options
- The answer must exactly match one of the options
- Questions should test comprehension and application, not only definitions
- Explanations should be 1-2 concise sentences and grounded in the document
- Cover different parts of the document

Document:
{text}""", temperature=0.1, max_completion_tokens=2200)

        quiz_items = extract_json_array(raw)
        if not isinstance(quiz_items, list):
            raise ValueError('Response is not a list')

        normalized = []
        for item in quiz_items:
            if not isinstance(item, dict):
                continue

            question = str(item.get('question', '')).strip()
            options = item.get('options', item.get('choices', []))
            if not isinstance(options, list):
                continue
            options = [str(option).strip() for option in options if str(option).strip()]

            answer = item.get('answer', item.get('correct_answer', ''))
            if isinstance(answer, int) and 0 <= answer < len(options):
                answer = options[answer]
            answer = str(answer).strip()

            if question and len(options) == 4 and answer in options:
                normalized.append({
                    'question': question,
                    'options': options,
                    'answer': answer,
                    'explanation': str(item.get('explanation', '')).strip()
                })

        if not normalized:
            raise ValueError('No valid quiz questions generated')

        if file_id:
            history_entry = History(
                action='quiz',
                file_id=int(file_id),
                filename=filename,
                generated_content=json.dumps(normalized),
                user_id=session['user_id']
            )
            db.session.add(history_entry)
            db.session.commit()

        return jsonify({'success': True, 'quiz': normalized, 'filename': filename})

    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Failed to parse quiz: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'AI quiz generation failed: {str(e)}'}), 500

#---------------------History API-------------------------------------------------------------
@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    query = History.query
    is_admin_view = current_user_is_admin()
    if not is_admin_view:
        query = query.filter_by(user_id=session['user_id'])
    records = query \
        .order_by(History.created_at.desc()) \
        .all()

    result = []
    for h in records:
        generated_available = bool(h.generated_content)
        content_available = generated_available
        if h.file_id:
            doc = db.session.get(File, h.file_id)
            content_available = content_available or bool(doc and can_view_owner_data(doc.user_id) and doc.content)

        history_data = {
            'id': h.id,
            'action': h.action,
            'file_id': h.file_id,
            'filename': h.filename,
            'time': h.created_at.strftime('%Y-%m-%d %H:%M'),
            'content_available': content_available,
            'generated_available': generated_available,
            'read_only': h.user_id != session['user_id']
        }
        if is_admin_view:
            history_data.update(owner_details(h.user_id))
        result.append(history_data)

    return jsonify({'success': True, 'history': result, 'admin_view': is_admin_view})

@app.route('/api/history/<int:history_id>', methods=['GET'])
@login_required
def get_history_detail(history_id):
    record = db.session.get(History, history_id)
    if not record or not can_view_owner_data(record.user_id):
        return jsonify({'success': False, 'error': 'History item not found'}), 404

    record_owner = owner_details(record.user_id) if current_user_is_admin() else {}
    access_metadata = {
        'read_only': record.user_id != session['user_id'],
        **record_owner
    }

    generated_items = None
    if record.generated_content:
        try:
            generated_items = json.loads(record.generated_content)
        except (TypeError, ValueError):
            generated_items = None

    if not record.file_id:
        return jsonify({
            'success': True,
            'id': record.id,
            'action': record.action,
            'filename': record.filename,
            'time': record.created_at.strftime('%Y-%m-%d %H:%M'),
            'generated_items': generated_items,
            'content': '',
            'message': 'No document is linked to this history item.',
            **access_metadata
        })

    doc = db.session.get(File, record.file_id)
    if not doc or not can_view_owner_data(doc.user_id):
        return jsonify({
            'success': True,
            'id': record.id,
            'action': record.action,
            'filename': record.filename,
            'time': record.created_at.strftime('%Y-%m-%d %H:%M'),
            'generated_items': generated_items,
            'content': '',
            'message': 'The linked document is no longer available.',
            **access_metadata
        })

    return jsonify({
        'success': True,
        'id': record.id,
        'action': record.action,
        'filename': doc.original_filename or record.filename or doc.filename,
        'time': record.created_at.strftime('%Y-%m-%d %H:%M'),
        'filetype': doc.filetype,
        'word_count': doc.word_count,
        'generated_items': generated_items,
        'content': doc.content or '',
        **access_metadata
    })

@app.route('/api/history/<int:history_id>', methods=['DELETE'])
@login_required
def delete_history_item(history_id):
    record = db.session.get(History, history_id)
    if not record or record.user_id != session['user_id']:
        return jsonify({'success': False, 'error': 'History item not found'}), 404

    db.session.delete(record)
    db.session.commit()
    return jsonify({'success': True})

#---------------------Page route-------------------------------------------------------------
@app.route('/')
def landing():
    return render_template('start.html')

@app.route('/home')
@login_required
def home():
    if current_user_is_admin():
        return redirect(url_for('admin_dashboard'))
    return render_template('Index.html')

@app.route('/admin')
@admin_required
def admin_dashboard():
    return render_template('admin.html')

@app.route('/login')
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))  

    return render_template('login.html')

@app.route('/register')
def register_page():
    if 'user_id' in session:
        return redirect(url_for('home'))  
    return render_template('register.html')

@app.route('/forgot-password')
def forgot_password_page():
    return render_template('forgot_password.html')

@app.route('/uploaddocument')
@login_required
def upload_document():
    return render_template('uploaddocument.html')

@app.route("/flashcards")
@login_required
def flashcards():
    return render_template('flashcards.html')

@app.route("/quiz")
@login_required
def quiz():
    return render_template('quiz.html')

@app.route("/summarize")
@login_required
def summarize():
    return render_template('summary.html')

@app.route("/history")
@login_required
def history():
    return render_template('history.html')



#--------------------------------------------------------------------------------------------

def ensure_db_schema():
    inspector = inspect(db.engine)
    if inspector.has_table('user'):
        columns = [col['name'] for col in inspector.get_columns('user')]
        if 'role' not in columns:
            db.session.execute(text("ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'client'"))
            db.session.commit()
        db.session.execute(text("UPDATE user SET role = 'client' WHERE role IS NULL OR role = ''"))
        for admin_email in configured_admin_emails():
            db.session.execute(
                text("UPDATE user SET role = 'admin' WHERE lower(email) = :email"),
                {'email': admin_email}
            )
        db.session.commit()
    if inspector.has_table('file'):
        columns = [col['name'] for col in inspector.get_columns('file')]
        if 'original_filename' not in columns:
            db.session.execute(text('ALTER TABLE file ADD COLUMN original_filename VARCHAR(255)'))
            db.session.commit()
        if 'filetype' not in columns:
            db.session.execute(text('ALTER TABLE file ADD COLUMN filetype VARCHAR(50)'))
            db.session.commit()
        if 'content' not in columns:
            db.session.execute(text('ALTER TABLE file ADD COLUMN content TEXT'))
            db.session.commit()
    if inspector.has_table('history'):
        columns = [col['name'] for col in inspector.get_columns('history')]
        if 'generated_content' not in columns:
            db.session.execute(text('ALTER TABLE history ADD COLUMN generated_content TEXT'))
            db.session.commit()

def initialize_database():
    with app.app_context():
        db.create_all()
        ensure_db_schema()

initialize_database()

if __name__ == '__main__':
    app.run(debug=True)
