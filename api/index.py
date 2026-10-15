import sys
from pathlib import Path

# Add project src directory to sys.path so modules resolve on Vercel
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "ecotwin" / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import app

# WSGI Middleware to ensure PATH_INFO matches the actual client requested route
# on Vercel's serverless environment, resolving internal rewrite path overrides
class VercelPathMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "")
        # If Vercel rewrote PATH_INFO to the function name, recover the true client path
        if path_info in ("/api/index", "/api/index.py", "/api"):
            forwarded = (
                environ.get("HTTP_X_FORWARDED_URI")
                or environ.get("HTTP_X_MATCHED_PATH")
                or environ.get("HTTP_X_REAL_PATH")
                or "/"
            )
            environ["PATH_INFO"] = forwarded.split("?")[0]
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathMiddleware(app.wsgi_app)
