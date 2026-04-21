from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, url_for

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

    def repr(self) -> str:
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

if __name__ == '_main_':
    app.run(debug=True)
    