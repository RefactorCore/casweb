from flask import Flask
from flask_login import LoginManager
from models import db, User
from config import Config
from datetime import datetime

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    db.init_app(app)

    # --- Login Manager ---
    login_manager = LoginManager()
    login_manager.login_view = 'login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ✅ Register the 'money' filter BEFORE returning the app
    @app.template_filter('money')
    def money(value):
        """Format a number as currency."""
        try:
            return f"₱{float(value):,.2f}"
        except (ValueError, TypeError):
            return "₱0.00"

    # --- Blueprints ---
    from routes.core import core_bp
    from routes.ar_ap import ar_ap_bp
    from routes.reports import reports_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(ar_ap_bp)
    app.register_blueprint(reports_bp)

    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)
