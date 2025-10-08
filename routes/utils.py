from flask import request

def paginate_query(query, per_page=20):
    """Paginate SQLAlchemy query based on ?page= parameter."""
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination
