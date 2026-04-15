from flask import Flask , render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_scss import Scss
from datetime import datetime

app = Flask(__name__)
Scss(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

#Database model
class Mytask(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    content = db.Column(db.String(100), nullable = False)
    completed = db.Column(db.Integer, default = 0)
    created = db.Column(db.DateTime, default = datetime.utcnow)

    def __repr__(self) -> str:
        return f" Task {self.id} "

with app.app_context():
    db.create_all()


#Homepage
@app.route('/', methods = ['POST', 'GET'])
def index():
    #Add a task
    if request.method == 'POST':
        current_task = request.form['content']
        new_task = Mytask(content=current_task)
        try:
            db.session.add(new_task)
            db.session.commit()
            return redirect('/')
        
        except Exception as e:
            print(f"Error: {e}")
            return f"Error: {e}"
    #See all tasks
    else:
        tasks = Mytask.query.order_by(Mytask.created).all()
        return render_template('index.html', tasks = tasks)
    
#Delete a task
@app.route('/delete/<int:id>')
def delete(id:int):
    delete_task = Mytask.query.get_or_404(id)
    try:
        db.session.delete(delete_task)
        db.session.commit()
        return redirect('/')
    
    except Exception as e:
        print(f"Error: {e}")
        return f"Error: {e}"

#Edit a task
@app.route('/edit/<int:id>', methods = ['POST', 'GET'])
def edit(id:int):
    task = Mytask.query.get_or_404(id)
    if request.method == 'POST':
        task.content = request.form['content']
        try:
            db.session.commit()
            return redirect('/')
        except Exception as e:
            print(f"Error: {e}")
            return f"Error: {e}"
    else:
        return render_template('edit.html', task=task)


if __name__ == '__main__':
    app.run(debug=True)