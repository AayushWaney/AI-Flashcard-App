from flask import Flask
import os
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy

# Load the environment variables from the .env file
load_dotenv()

# 1. INITIALIZATION OF THE APP FIRST
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///flashcards.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 2. INITIALIZATION OF EXTENSIONS (DB)
db = SQLAlchemy(app)


# 6. RUNNING THE SERVER
if __name__ == "__main__":
    app.run(debug=True)