from flask import Flask, render_template, request, redirect, url_for, flash, make_response, jsonify
import os
from dotenv import load_dotenv
import csv
from io import StringIO
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, current_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date, timezone
import google.generativeai as genai
import PyPDF2
import json

# Load the environment variables from the .env file
load_dotenv()


# 1. INITIALIZATION OF THE APP FIRST

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

# RENDER POSTGRESQL

db_url = os.environ.get('DATABASE_URL')

if db_url:

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # If no URL is found, fallback to local SQLite for testing on your Mac
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///flashcards.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize Gemini AI Client
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))


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
    decks = db.relationship('Deck', backref='owner', lazy=True, cascade="all, delete-orphan")

    # New Study Statistics Columns
    total_reviews = db.Column(db.Integer, default=0)
    correct_reviews = db.Column(db.Integer, default=0)
    current_streak = db.Column(db.Integer, default=0)
    last_study_date = db.Column(db.Date, nullable=True)


class Deck(db.Model):
    __tablename__ = 'decks'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cards = db.relationship('Card', backref='deck', lazy=True, cascade="all, delete-orphan")



class Card(db.Model):
    __tablename__ = 'cards'
    id = db.Column(db.Integer, primary_key=True)
    front = db.Column(db.Text, nullable=False)
    back = db.Column(db.Text, nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey('decks.id'), nullable=False)

    # New Spaced Repetition Columns
    next_review = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    ease_factor = db.Column(db.Float, default=2.5)
    repetitions = db.Column(db.Integer, default=0)
    interval = db.Column(db.Integer, default=0)  # Days until next review



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
            flash("You've already signed up with that email, log in instead!", "warning")
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
            flash("That email does not exist, please try again.", "danger")
            return redirect(url_for('login'))
        elif not check_password_hash(user.password_hash, password):
            flash('Password incorrect, please try again.', "danger")
            return redirect(url_for('login'))
        else:
            login_user(user)
            return redirect(url_for('dashboard'))

    return render_template("login.html")

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

        # Create the new deck and link it to the current user
        new_deck = Deck(title=title, description=description, user_id=current_user.id)
        db.session.add(new_deck)
        db.session.commit()

        flash("Deck created successfully!", "success")
        return redirect(url_for('dashboard'))

    return render_template('create_deck.html')


@app.route('/deck/<int:deck_id>', methods=['GET'])
@login_required
def view_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    if deck.user_id != current_user.id:
        flash("You do not have permission to view this deck.", "danger")
        return redirect(url_for('dashboard'))

    # Only fetch cards due for review to power the Study button count
    now = datetime.now(timezone.utc)
    due_cards = Card.query.filter(Card.deck_id == deck.id, Card.next_review <= now).all()
    # Pass 'due_cards' to the template.
    # The bottom list can keep using 'deck.cards' automatically.
    return render_template('view_deck.html', deck=deck, cards=due_cards)

# STUDY SESSION ROUTE
@app.route('/deck/<int:deck_id>/study')
@login_required
def study_session(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    if deck.user_id != current_user.id:
        return redirect(url_for('dashboard'))

    # Fetch only the cards due for review, order by the oldest due date first
    now = datetime.now(timezone.utc)
    active_cards = Card.query.filter(
        Card.deck_id == deck.id,
        Card.next_review <= now
    ).order_by(Card.next_review.asc()).all()

    if not active_cards:
        flash("🎉 You're all caught up! No cards due for review right now.", "success")
        return redirect(url_for('view_deck', deck_id=deck.id))

    # Grab the very first card in the list to show the user
    current_card_to_study = active_cards[0]
    total_due = len(active_cards)

    # Pass the single 'card' and the 'total_due' count to the template
    return render_template('study_session.html', deck=deck, card=current_card_to_study, total_due=total_due)


# RATE CARD ROUTE
@app.route('/card/<int:card_id>/rate', methods=['POST'])
@login_required
def rate_card(card_id):
    card = Card.query.get_or_404(card_id)

    if card.deck.user_id != current_user.id:
        return redirect(url_for('dashboard'))

    # 1. Grab the rating from the HTML form buttons (0=Again, 1=Hard, 2=Good, 3=Easy)
    rating = request.form.get('rating', type=int)

    # If the browser failed to send the rating, stop here before the math crashes
    if rating is None:
        flash("Something went wrong with that button click. Please try again.", "warning")
        return redirect(url_for('study_session', deck_id=card.deck_id))

    # 2. SM-2 Algorithm Logic (Updated for 0-3 scale)
    if rating == 0:
        card.repetitions = 0
        card.interval = 1
    else:
        if card.repetitions == 0:
            card.interval = 1
        elif card.repetitions == 1:
            card.interval = 6
        else:
            card.interval = int(round(card.interval * card.ease_factor))
        card.repetitions += 1

    # Adjust Ease Factor based on the 0-3 rating
    card.ease_factor = card.ease_factor + (0.1 - (3 - rating) * (0.08 + (3 - rating) * 0.02))
    if card.ease_factor < 1.3:
        card.ease_factor = 1.3

    card.next_review = datetime.now(timezone.utc) + timedelta(days=card.interval)

    # 3. STATS & STREAK LOGIC
    user = current_user
    user.total_reviews += 1

    # If they clicked Hard(1), Good(2), or Easy(3), it counts as a correct review
    if rating > 0:
        user.correct_reviews += 1

    # Streak Logic: Check if they studied today or yesterday
    today = datetime.now(timezone.utc).date()
    if user.last_study_date != today:
        if user.last_study_date == today - timedelta(days=1):
            user.current_streak += 1  # Continued streak
        else:
            user.current_streak = 1  # Broke the streak, reset to 1
        user.last_study_date = today


    db.session.commit()
    return redirect(url_for('study_session', deck_id=card.deck_id))

@app.route('/deck/<int:deck_id>/add_card', methods=['GET', 'POST'])
@login_required
def add_card(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    if deck.user_id != current_user.id:
        flash("You do not have permission to add cards to this deck.", "danger")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        front = request.form.get('front')
        back = request.form.get('back')

        # Create the card and link it to the deck
        new_card = Card(front=front, back=back, deck_id=deck.id)
        db.session.add(new_card)
        db.session.commit()

        flash("Card added successfully!", "success")
        # Redirect back to the view deck page so they can see their new card
        return redirect(url_for('view_deck', deck_id=deck.id))

    return render_template('add_card.html', deck=deck)


# EXPORT ROUTE
@app.route('/export/<int:deck_id>')
@login_required
def export_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != current_user.id:
        return redirect(url_for('dashboard'))


    si = StringIO()
    cw = csv.writer(si)

    # Write the header row
    cw.writerow(['Front', 'Back'])

    # Write all the cards
    for card in deck.cards:
        cw.writerow([card.front, card.back])

    # Convert the string into a downloadable file response
    output = make_response(si.getvalue())
    safe_title = deck.title.replace(' ', '_')
    output.headers["Content-Disposition"] = f"attachment; filename={safe_title}_flashcards.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# IMPORT ROUTE
@app.route('/import', methods=['GET', 'POST'])
@login_required
def import_deck():
    if request.method == 'POST':
        title = request.form.get('title')
        file = request.files.get('file')

        if not file or file.filename == '':
            flash("No file selected.", "warning")
            return redirect(request.url)

        if not file.filename.endswith('.csv'):
            flash("Please upload a .csv file.", "warning")
            return redirect(request.url)

        # 1. Create the new deck first
        new_deck = Deck(title=title, description="Imported from CSV", user_id=current_user.id)
        db.session.add(new_deck)
        db.session.flush()  # This assigns an ID to new_deck without committing yet

        # 2. Read and parse the CSV file
        stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.reader(stream)

        # Skip the header row (assuming row 1 is 'Front, Back')
        next(csv_reader, None)

        # 3. Create cards from the rows
        cards_added = 0
        for row in csv_reader:
            if len(row) >= 2:  # Ensure the row actually has a front and back
                new_card = Card(front=row[0], back=row[1], deck_id=new_deck.id)
                db.session.add(new_card)
                cards_added += 1

        db.session.commit()
        flash(f"Success! Imported {cards_added} cards into '{title}'.", "success")
        return redirect(url_for('dashboard'))

    return render_template('import_deck.html')


# RESET DECK ROUTE
@app.route('/reset_deck/<int:deck_id>', methods=['POST'])
@login_required
def reset_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    # Security check
    if deck.user_id != current_user.id:
        flash("You do not have permission to reset this deck.", "danger")
        return redirect(url_for('dashboard'))

    # Reset every card in the deck back to factory settings
    now = datetime.now(timezone.utc)
    for card in deck.cards:
        card.next_review = now
        card.ease_factor = 2.5
        card.repetitions = 0
        card.interval = 0

    db.session.commit()
    flash(f"Success! Progress for '{deck.title}' has been reset.", "success")
    return redirect(url_for('dashboard'))


# EDIT DECK ROUTE
@app.route('/deck/<int:deck_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    # Security check to prevent users from editing someone else's deck
    if deck.user_id != current_user.id:
        flash("You do not have permission to edit this deck.", "danger")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        deck.title = request.form.get('title')
        deck.description = request.form.get('description')
        db.session.commit()
        flash(f"Deck '{deck.title}' updated successfully!", "success")
        return redirect(url_for('dashboard'))

    return render_template('edit_deck.html', deck=deck)


# DELETE DECK ROUTE
@app.route('/deck/<int:deck_id>/delete', methods=['POST'])
@login_required
def delete_deck(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    # Security check
    if deck.user_id == current_user.id:


        db.session.delete(deck)
        db.session.commit()
        flash(f"Deck '{deck.title}' has been deleted.", "success")

    return redirect(url_for('dashboard'))


# EDIT CARD ROUTE
@app.route('/card/<int:card_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_card(card_id):
    card = Card.query.get_or_404(card_id)

    # Security check: ensure the deck this card belongs to is owned by the user
    if card.deck.user_id != current_user.id:
        flash("You do not have permission to edit this card.", "danger")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        card.front = request.form.get('front')
        card.back = request.form.get('back')
        db.session.commit()

        flash("Card updated successfully!", "success")
        return redirect(url_for('view_deck', deck_id=card.deck_id))

    return render_template('edit_card.html', card=card)


# DELETE CARD ROUTE
@app.route('/card/<int:card_id>/delete', methods=['POST'])
@login_required
def delete_card(card_id):
    card = Card.query.get_or_404(card_id)
    deck_id = card.deck_id

    # Security check
    if card.deck.user_id == current_user.id:
        db.session.delete(card)
        db.session.commit()
        flash("Card deleted permanently.", "success")

    return redirect(url_for('view_deck', deck_id=deck_id))


# AI IMPROVE CARD ROUTE
@app.route('/card/<int:card_id>/improve_ai', methods=['POST'])
@login_required
def improve_card_ai(card_id):
    card = Card.query.get_or_404(card_id)

    # Security check
    if card.deck.user_id != current_user.id:
        flash("You do not have permission to edit this card.", "danger")
        return redirect(url_for('dashboard'))

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')

        prompt = f"""
        Improve this flashcard for learning efficiency. 
        Make the question clearer and the answer more concise.
        Return ONLY a valid JSON object with EXACTLY two keys: "front" and "back".
        Do not include markdown formatting like ```json.

        Current Front: {card.front}
        Current Back: {card.back}
        """

        response = model.generate_content(prompt)

        # Clean the response just in case
        raw_json = response.text.strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]

        ai_data = json.loads(raw_json.strip())

        # Update the database if the AI followed instructions
        if 'front' in ai_data and 'back' in ai_data:
            card.front = ai_data['front']
            card.back = ai_data['back']
            db.session.commit()
            flash("✨ Card successfully improved by AI!", "success")
        else:
            flash("AI did not return the expected format. Please try again.", "danger")

    except json.JSONDecodeError:
        flash("The AI returned unreadable data. Please try again.", "danger")
    except Exception as e:
        flash(f"API Error: Make sure your Gemini API key is correct. ({str(e)})", "danger")

    return redirect(url_for('view_deck', deck_id=card.deck_id))


# AI DECK SUMMARY ROUTE
@app.route('/deck/<int:deck_id>/summary', methods=['GET'])
@login_required
def summarize_deck_ai(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    # Security check
    if deck.user_id != current_user.id:
        flash("You do not have permission to view this deck.", "danger")
        return redirect(url_for('dashboard'))

    if not deck.cards:
        flash("This deck is empty. Add some cards before summarizing!", "warning")
        return redirect(url_for('view_deck', deck_id=deck.id))

    # Gather all card text to send to Gemini
    deck_content = ""
    for card in deck.cards:
        deck_content += f"Q: {card.front}\nA: {card.back}\n\n"

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')

        # ask Gemini to return raw HTML so we can render it
        prompt = f"""
        You are an expert tutor. I will provide you with a list of flashcards from a deck titled "{deck.title}".
        Please provide a concise, high-level summary of the key concepts covered in this deck.
        Format your response using ONLY basic HTML tags (like <h3>, <ul>, <li>, and <strong>). 
        Do NOT use markdown (no asterisks or hashtags).

        Flashcards:
        {deck_content[:30000]}
        """

        response = model.generate_content(prompt)
        summary_html = response.text.strip()

        # pass the generated HTML directly to a new template
        return render_template('deck_summary.html', deck=deck, summary=summary_html)

    except Exception as e:
        flash(f"API Error: Make sure your Gemini API key is correct. ({str(e)})", "danger")
        return redirect(url_for('view_deck', deck_id=deck.id))


# AI FLASHCARD GENERATOR ROUTE
@app.route('/generate_ai', methods=['GET', 'POST'])
@login_required
def generate_ai_cards():
    if request.method == 'GET':
        # need to pass the user's decks to the dropdown menu
        decks = Deck.query.filter_by(user_id=current_user.id).all()
        return render_template('generate_ai.html', decks=decks)

    deck_id = request.form.get('deck_id')
    notes = request.form.get('notes', '').strip()
    pdf_file = request.files.get('pdf_file')

    if not deck_id:
        flash("Please select a target deck.", "warning")
        return redirect(request.url)

    # Verify the deck actually belongs to the user
    deck = Deck.query.get_or_404(deck_id)
    if deck.user_id != current_user.id:
        return redirect(url_for('dashboard'))

    extracted_text = ""

    # 1. Try to read the PDF if one was uploaded
    if pdf_file and pdf_file.filename.endswith('.pdf'):
        try:
            pdf_reader = PyPDF2.PdfReader(pdf_file.stream)
            for page in pdf_reader.pages:
                # Extract text page by page
                page_text = page.extract_text()
                if page_text:
                    extracted_text += page_text + "\n"
        except Exception as e:
            flash(f"An unexpected error occurred: {str(e)}", "danger")
            return redirect(request.url)

    # 2. Append any raw pasted notes to the text
    if notes:
        extracted_text += "\n" + notes

    if len(extracted_text) < 20:
        flash("Not enough text provided. Please upload a PDF or paste more detailed notes.", "warning")
        return redirect(request.url)

    # 3. Send the text to Gemini
    try:
        # Use flash because it is lightning fast and perfect for text tasks
        model = genai.GenerativeModel('gemini-2.5-flash')

        prompt = f"""
        You are an expert study assistant. I will provide you with text from a lecture or document. 
        Your task is to extract the most important concepts and turn them into flashcards.

        Rules:
        1. Return ONLY a valid JSON array of objects. Do NOT wrap it in markdown block quotes like ```json.
        2. Each object must have exactly two keys: "front" (the question) and "back" (the concise answer).
        3. Generate up to 10 high-quality flashcards based on the text.

        Text to analyze:
        {extracted_text[:20000]} 
        """
        # Note: We limit the prompt to the first 20k characters just to keep it snappy.

        response = model.generate_content(prompt)

        # Clean the response just in case the AI added Markdown formatting
        raw_json = response.text.strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]

        ai_data = json.loads(raw_json.strip())

        # 4. Save the generated cards to the database
        cards_added = 0
        if isinstance(ai_data, list):
            for item in ai_data:
                if 'front' in item and 'back' in item:
                    new_card = Card(front=item['front'], back=item['back'], deck_id=deck.id)
                    db.session.add(new_card)
                    cards_added += 1

            db.session.commit()
            flash(f"Success! Gemini AI generated {cards_added} flashcards from your material.", "success")
            return redirect(url_for('view_deck', deck_id=deck.id))
        else:
            raise ValueError("AI did not return a list.")

    except json.JSONDecodeError:
        flash("The AI returned data in an unreadable format. Please try clicking generate again.", "danger")
        return redirect(request.url)
    except Exception as e:
        flash(f"API Error: Make sure your Gemini API key is correct. ({str(e)})", "danger")
        return redirect(request.url)



# REST API ENDPOINTS


# 1. GET ALL DECKS
@app.route('/api/decks', methods=['GET'])
@login_required
def api_get_decks():
    # Return a JSON list of all the user's decks
    decks = current_user.decks
    decks_data = []
    for d in decks:
        decks_data.append({
            "id": d.id,
            "title": d.title,
            "description": d.description,
            "card_count": len(d.cards)
        })
    return jsonify({"decks": decks_data}), 200


# 2. GET ALL CARDS IN A DECK
@app.route('/api/decks/<int:deck_id>/cards', methods=['GET'])
@login_required
def api_get_cards(deck_id):
    deck = Deck.query.get_or_404(deck_id)

    # Security check
    if deck.user_id != current_user.id:
        return jsonify({"error": "Unauthorized access to this deck"}), 403

    cards_data = []
    for c in deck.cards:
        cards_data.append({
            "id": c.id,
            "front": c.front,
            "back": c.back,
            "ease_factor": c.ease_factor,
            "interval": c.interval
        })
    return jsonify({"deck": deck.title, "cards": cards_data}), 200


# 3. POST A NEW CARD
@app.route('/api/cards', methods=['POST'])
@login_required
def api_create_card():
    # Expects a JSON payload instead of a standard HTML form
    data = request.get_json()

    if not data or 'deck_id' not in data or 'front' not in data or 'back' not in data:
        return jsonify({"error": "Missing required fields (deck_id, front, back)"}), 400

    deck = Deck.query.get(data['deck_id'])
    if not deck or deck.user_id != current_user.id:
        return jsonify({"error": "Deck not found or unauthorized"}), 403

    new_card = Card(front=data['front'], back=data['back'], deck_id=deck.id)
    db.session.add(new_card)
    db.session.commit()

    return jsonify({"message": "Card successfully created via API", "card_id": new_card.id}), 201

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('home'))



# 6. RUN THE SERVER

if __name__ == "__main__":
    app.run(debug=True)