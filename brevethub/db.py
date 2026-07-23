import psycopg2
import psycopg2.extras
from flask import g, current_app


def get_db():
    if 'db' not in g:
        g.db = psycopg2.connect(current_app.config['DATABASE_URL'])
        g.db.autocommit = False
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        if not db.closed:
            db.close()


def init_app(app):
    app.teardown_appcontext(close_db)
