import os
from datetime import datetime

from flask import request, g
from flask_cors import CORS
from flask_wtf import CSRFProtect

from app import create_app, AUTHENTICATED_ROLE
from app.language import words

app = create_app()
app.config["SECRET_KEY"] = os.getenv('SECRET')
CORS(app, origins=[os.getenv('CORS_WEB')])
csrf = CSRFProtect(app)


@app.template_filter('datetime_format')
def datetime_format(value):
    """Converts milliseconds timestamp to a readable date format."""
    return datetime.utcfromtimestamp(value / 1000).strftime('%Y-%m-%d %H:%M:%S')


@app.context_processor
def inject_primary_color():
    """Inject primary_color from cookies into all templates."""
    return {
        "_primary_color": request.cookies.get('primary_color', '#ffffff'),
        "_username": request.cookies.get('username', None),
        "AUTHENTICATED_ROLE": AUTHENTICATED_ROLE,
        "_words": words.get(request.cookies.get('lang', 'en'), words.get('en')),
        "_host": os.getenv('HOST'),
    }


if __name__ == "__main__":
    app.run()
