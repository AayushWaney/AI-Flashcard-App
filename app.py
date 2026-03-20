from flask import Flask, render_template, request, redirect, url_for, flash
import os
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, current_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# Load the environment variables from the .env file
load_dotenv()

# 1. INITIALIZATION OF THE APP FIRST
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///flashcards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 2. INITIALIZATION OF EXTENSIONS (DB & Login)
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 3. DATABASE MODELS
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    decks = db.relationship('Deck', backref='owner', lazy=True)

    # Study Statistics
    total_reviews = db.Column(db.Integer, default=0)
    correct_reviews = db.Column(db.Integer, default=0)
    current_streak = db.Column(db.Integer, default=0)
    last_study_date = db.Column(db.Date, nullable=True)

class Deck(db.Model):
    __tablename__ = 'decks'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cards = db.relationship('Card', backref='deck', lazy=True, cascade="all, delete-orphan")

class Card(db.Model):
    __tablename__ = 'cards'
    id = db.Column(db.Integer, primary_key=True)
    front = db.Column(db.Text, nullable=False)
    back = db.Column(db.Text, nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey('decks.id'), nullable=False)

    # Spaced Repetition Logic
    next_review = db.Column(db.DateTime, default=datetime.utcnow)
    ease_factor = db.Column(db.Float, default=2.5)
    repetitions = db.Column(db.Integer, default=0)
    interval = db.Column(db.Integer, default=0)

# 4. CREATE TABLES
with app.app_context():
    db.create_all()

# 5. ROUTES
@app.route('/')
def home():
    # If the user is already logged in, send them straight to the dashboard.
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if User.query.filter_by(email=email).first():
            flash("You've already signed up with that email, log in instead!")
            return redirect(url_for('login'))

        hash_and_salted_password = generate_password_hash(
            password,
            method='pbkdf2:sha256',
            salt_length=8
        )

        new_user = User(
            username=username,
            email=email,
            password_hash=hash_and_salted_password
        )
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)
        return redirect(url_for('dashboard'))

    return render_template("register.html")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if not user:
            flash("That email does not exist, please try again.")
            return redirect(url_for('login'))
        elif not check_password_hash(user.password_hash, password):
            flash('Password incorrect, please try again.')
            return redirect(url_for('login'))
        else:
            login_user(user)
            return redirect(url_for('dashboard'))

    return render_template("login.html")

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Fetch all decks belonging to the currently logged-in user
    user_decks = current_user.decks
    return render_template('dashboard.html', decks=user_decks)

@app.route('/create_deck', methods=['GET', 'POST'])
@login_required
def create_deck():
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        new_deck = Deck(title=title, description=description, user_id=current_user.id)
        db.session.add(new_deck)
        db.session.commit()
        flash("Deck created successfully!")
        return redirect(url_for('dashboard'))
    return render_template('create_deck.html')

@app.route('/deck/<int:deck_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != current_user.id:
        flash("You do not have permission to edit this deck.")
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        deck.title = request.form.get('title')
        deck.description = request.form.get('description')
        db.session.commit()
        flash(f"Deck '{deck.title}' updated successfully!")
        return redirect(url_for('dashboard'))
    return render_template('edit_deck.html', deck=deck)

@app.route('/deck/<int:deck_id>/delete', methods=['POST'])
@login_required
def delete_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id == current_user.id:
        db.session.delete(deck)
        db.session.commit()
        flash(f"Deck '{deck.title}' has been deleted.")
    return redirect(url_for('dashboard'))

# 6. RUNNING THE SERVER
if __name__ == "__main__":
    app.run(debug=True)