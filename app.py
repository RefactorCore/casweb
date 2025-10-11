# --- FIX: Import the necessary functions ---
from flask import Flask, redirect, url_for, request
# --- FIX: Import current_user ---
from flask_login import LoginManager, current_user
from routes.accounts import accounts_bp


from models import db, User, CompanyProfile
from config import Config
from datetime import datetime

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.register_blueprint(accounts_bp)

    db.init_app(app)

    # --- Login Manager ---
    login_manager = LoginManager()
    # --- FIX: Point to the correct blueprint endpoint ---
    login_manager.login_view = 'core.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- 'money' filter ---
    @app.template_filter('money')
    def money(value):
        """Format a number as currency."""
        try:
            return f"₱{float(value):,.2f}"
        except (ValueError, TypeError):
            return "₱0.00"

    @app.before_request
    def check_setup():
        # Allow access to setup pages and static files without redirection
        if request.endpoint and request.endpoint.startswith(('core.setup', 'static')):
            return

        # If user is not authenticated and is trying to access anything else, let login handle it
        if not current_user.is_authenticated and request.endpoint != 'core.login':
             # Check for Company Profile first
            if not CompanyProfile.query.first():
                return redirect(url_for('core.setup_license'))
            # Check for Admin User next
            elif not User.query.filter_by(role='Admin').first():
                 return redirect(url_for('core.setup_license')) # Start from step 1

    # --- Context Processor ---
    @app.context_processor
    def inject_company_profile():
        """Injects company profile data into all templates."""
        company = CompanyProfile.query.first()
        return dict(company=company)

    # --- Blueprints ---
    from routes.core import core_bp
    from routes.ar_ap import ar_ap_bp
    from routes.reports import reports_bp
    from routes.users import user_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(ar_ap_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(user_bp)

    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)