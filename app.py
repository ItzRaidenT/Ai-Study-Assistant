from functools import wraps
from flask import Flask, json, render_template, jsonify, request, session, url_for
from werkzeug.utils import redirect, secure_filename
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from datetime import datetime
import google.generativeai as genai
import pdfplumber
import os
import re
import uuid

app = Flask(__name__, static_folder='Static', template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config["UPLOAD_FOLDER"] = "uploads/"
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB
app.secret_key = 'Itz_1001_Mid'  # Add secret key for sessions
genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
db = SQLAlchemy(app)

ALLOWED_EXTENSIONS = {'pdf', 'txt'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class user(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(80), nullable = False, unique = True)
    email = db.Column(db.String(80), nullable = False, unique = True)
    password = db.Column(db.String(225), nullable = False)

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

def create_user(username, email, password):
    try:
        hashed_password = generate_password_hash(password)
        new_user = user(username=username, email=email, password=hashed_password)
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
    documents = File.query.filter_by(user_id=session['user_id']) \
        .order_by(File.uploaded_at.desc()) \
        .all()

    docs = []
    for doc in documents:
        docs.append({
            'id': doc.id,
            'filename': doc.original_filename or doc.filename,
            'stored_filename': doc.filename,
            'filetype': doc.filetype,
            'word_count': doc.word_count,
            'preview': get_preview(doc.content or ''),
            'uploaded_at': doc.uploaded_at.strftime('%Y-%m-%d %H:%M')
        })

    return jsonify({'success': True, 'documents': docs})

@app.route('/documents/<int:file_id>', methods=['GET'])
@login_required
def get_document(file_id):
    doc = db.session.get(File, file_id)
    if not doc or doc.user_id != session['user_id']:
        return jsonify({'success': False, 'error': 'Document not found'}), 404

    return jsonify({
        'success': True,
        'id': doc.id,
        'filename': doc.original_filename or doc.filename,
        'filetype': doc.filetype,
        'word_count': doc.word_count,
        'content': doc.content or '',
        'uploaded_at': doc.uploaded_at.strftime('%Y-%m-%d %H:%M')
    })

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
    return jsonify({'success': True, 'username': username}), 201

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
    session['username'] = user_obj.username
    return jsonify({'success': True, 'username': user_obj.username})

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



@app.route('/logout', methods = ['POST'])
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/me', methods = ['GET'])
@login_required
def me():
    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        return jsonify({'success': False, 'error': 'User not found'}), 404
    return jsonify({'success': True, 'id': user.id, 'username': user.username, 'email': user.email})

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
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(f"""Please summarize the following document clearly and concisely. 
Structure your summary with:
1. **Overview** (2-3 sentences on the main topic)
2. **Key Points** (bullet list of 4-6 main ideas)
3. **Conclusion** (1-2 sentences on the overall takeaway)

Document:
{text}""")

        summary = response.text

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
        return jsonify({'success': False, 'error': f'AI summarization failed: {str(e)}'}), 500
    
# -------------------------AI Flashcards API------------------------------------------------
@app.route('/api/flashcards', methods=['POST'])
@login_required
def ai_flashcards():
    data = request.get_json() or {}
    file_id = data.get('file_id') or data.get('doc_id')
    text = data.get('text', '').strip()
    count = min(int(data.get('count', 10)), 20)  # Max 20 cards

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
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(f"""Generate exactly {count} flashcards from the following document for studying.

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
{text}""")

        raw = response.text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```$', '', raw, flags=re.MULTILINE)
        raw = raw.strip()

        flashcards = json.loads(raw)

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
                user_id=session['user_id']
            )
            db.session.add(history_entry)
            db.session.commit()

        return jsonify({'success': True, 'flashcards': normalized, 'filename': filename})

    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Failed to parse flashcards: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'AI flashcard generation failed: {str(e)}'}), 500

#---------------------History API-------------------------------------------------------------
@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    records = History.query.filter_by(user_id=session['user_id']) \
        .order_by(History.created_at.desc()) \
        .all()

    result = []
    for h in records:
        result.append({
            'action': h.action,
            'filename': h.filename,
            'time': h.created_at.strftime('%Y-%m-%d %H:%M')
        })

    return jsonify({'success': True, 'history': result})

# -------------------------AI Flashcards API------------------------------------------------
@app.route('/api/flashcards', methods=['POST'])
@login_required
def ai_flashcards():
    data = request.get_json() or {}
    file_id = data.get('file_id') or data.get('doc_id')
    text = data.get('text', '').strip()
    count = min(int(data.get('count', 10)), 20)  # Max 20 cards

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
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(f"""Generate exactly {count} flashcards from the following document for studying.

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
{text}""")

        raw = response.text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r'^(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*$', '', raw, flags=re.MULTILINE)
        raw = raw.strip()

        flashcards = json.loads(raw)

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
                user_id=session['user_id']
            )
            db.session.add(history_entry)
            db.session.commit()

        return jsonify({'success': True, 'flashcards': normalized, 'filename': filename})

    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({'success': False, 'error': f'Failed to parse flashcards: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'AI flashcard generation failed: {str(e)}'}), 500

#---------------------Page route-------------------------------------------------------------
@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template('login.html')
    return render_template('Index.html')

@app.route('/login')
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/register')
def register_page():
    if 'user_id' in session:
        return redirect(url_for('index'))
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

@app.route("/summarize")
@login_required
def summarize():
    return render_template('summary.html')

@app.route("/history")
@login_required
def history():
    records = History.query.filter_by(user_id=session['user_id']) \
        .order_by(History.created_at.desc()) \
        .all()

    return render_template('history.html', history=records)



#--------------------------------------------------------------------------------------------

def ensure_db_schema():
    inspector = inspect(db.engine)
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_db_schema()
    app.run(debug=True)
    