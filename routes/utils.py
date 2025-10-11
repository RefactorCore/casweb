from flask import request
from flask_login import current_user
from models import db, AuditLog


def paginate_query(query, per_page=20):
    """Paginate SQLAlchemy query based on ?page= parameter."""
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination

def log_action(action_description):
    """A helper function to easily create an audit log entry."""
    log = AuditLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action_description,
        ip_address=request.remote_addr
    )
    db.session.add(log)