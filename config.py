import os

class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, 'data', 'team_asha.db')
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-prod')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'asha2026')
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'riders')
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB max upload
