import os

class Config:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE_URL = os.environ.get('DATABASE_URL')
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-prod')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'asha2026')
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'riders')
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB max upload
    
    # Google OAuth Configuration
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
    
    # Linear API Configuration
    LINEAR_API_KEY = os.environ.get('LINEAR_API_KEY')
    LINEAR_TEAM_ID = '33d7eaca-512f-4bac-b5cb-d6d61ac2fa74'
    LINEAR_LABEL_BUG = 'f5529bdf-573a-47d3-8027-3d0cb6732e61'
    LINEAR_LABEL_FEATURE = '93914cc6-28ef-4397-a109-fe38ecfc3160'

    # Session configuration for production security
    SESSION_COOKIE_SECURE = os.environ.get('VERCEL_ENV') == 'production'  # HTTPS only in prod
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to cookies
    SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
