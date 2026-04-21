from flask import Flask, render_template, jsonify, request, session
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, url_for
import pdfplumber
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config["UPLOAD_FOLDER"] = "uploads/"
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 #16MB
db = SQLAlchemy(app)

Allowed_extensions = {'txt', 'pdf'}

#--------------------------Database model-------------------------------------------------------
class userdata(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    email = db.Column(db.String(80), nullable = False)
    password = db.Column(db.String(80), nullable = False)

    def __repr__(self) -> str:
        return f" File {self.id} "
    
with app.app_context():
    db.create_all()

#-------------------------User authentication system------------------------------------------------
def validate_email(email):
    if '@' in email and '.' in email.split('@')[-1]:
        return True
    return False

#--------------------------User authentication system API------------------------------------------------
@app.route('/register', methods=['POST'])
def register():
    userid = request.form.get('userid').strip()
    email = request.form.get('email').strip().lower()
    password = request.form.get('password').strip()
    confirm_password = request.form.get('confirm_password').strip()

    if not email or not userid or not password or not confirm_password:
        return jsonify({'error': 'All fields are required'}), 400
    
    if len(userid)< 3 or len(userid) > 10:
        return jsonify({'error': 'User ID must be between 3 and 10 characters'}), 400
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters long'}), 400
    
    if not validate_email(email):
        return jsonify({'error': 'Invalid email format'}), 400

    if password != confirm_password:
        return jsonify({'error': 'Passwords do not match'}), 400

    if userdata.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 400

    new_user = userdata(email=email, password=password)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'success': True, 'message': 'User registered successfully'})

@app.route('/login', methods=['POST'])
def login_user():
    userid = request.form.get('userid').strip()
    email = request.form.get('email').strip().lower()
    password = request.form.get('password').strip()

    if not email or not userid or not password:
        return jsonify({'error': 'All fields are required'}), 400

    user = userdata.query.filter_by(email=email).first()
    if not user or user.password != password:
        return jsonify({'error': 'Invalid email or password'}), 400
    
    session['user_id'] = user.id

    return jsonify({'success': True, 'message': 'Logged in successfully'})

#--------------------------------------------------------------------------------------------

# --------------------------Upload file system -----------------------------------------------

def allow_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Allowed_extensions

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
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'})
    if not allow_file(file.filename):
        return jsonify({'error': 'File type not allowed'})
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename )
    file.save(filepath)

    extracted_text = extract_text(filepath)
    os.remove(filepath)

    if not extracted_text:
        return jsonify({'success': False, 'error': 'Could not extract text from file'}), 400

    return jsonify({
        'success': True,
        'filename': filename,
        'word_count': get_word_count(extracted_text),
        'preview': get_preview(extracted_text),
        'full_text': extracted_text
    })

@app.route('/upload', methods = ["DELETE"])
def delete_file():
    return "wait"
    


#---------------------Page route-------------------------------------------------------------
@app.route('/')
def index():
    if 'user_id' not in session:
        return render_template('login.html')
    return render_template('index.html')

@app.route('/login')
def login():
    if 'user_id' in session:
        return render_template('index.html')
    return render_template('login.html')

@app.route('/register')
def register_page():
    if 'user_id' in session:
        return render_template('index.html')
    return render_template('register.html') 

@app.route('/uploaddocument')
def upload_document():
    if 'user_id' in session:
        return render_template('uploaddocument.html')
    return render_template('login.html')  



#--------------------------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True)
    