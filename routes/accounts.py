from flask import Blueprint, render_template, request, flash, redirect, url_for
from models import db, Account
from flask_login import login_required
from .decorators import role_required

accounts_bp = Blueprint('accounts', __name__, url_prefix='/accounts')

@accounts_bp.route('/')
@login_required
@role_required('Admin', 'Accountant')
def chart_of_accounts():
    """Display and manage the Chart of Accounts."""
    accounts = Account.query.order_by(Account.code).all()
    return render_template('chart_of_accounts.html', accounts=accounts)

@accounts_bp.route('/add', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def add_account():
    """Add a new account."""
    code = request.form.get('code')
    name = request.form.get('name')
    type = request.form.get('type')

    if not code or not name or not type:
        flash('All fields are required.', 'danger')
        return redirect(url_for('accounts.chart_of_accounts'))

    if Account.query.filter_by(code=code).first() or Account.query.filter_by(name=name).first():
        flash('Account code or name already exists.', 'danger')
        return redirect(url_for('accounts.chart_of_accounts'))

    new_account = Account(code=code, name=name, type=type)
    db.session.add(new_account)
    db.session.commit()
    flash('Account added successfully.', 'success')
    return redirect(url_for('accounts.chart_of_accounts'))

@accounts_bp.route('/update/<int:account_id>', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def update_account(account_id):
    """Update an existing account."""
    account = Account.query.get_or_404(account_id)
    account.code = request.form.get('code')
    account.name = request.form.get('name')
    account.type = request.form.get('type')
    db.session.commit()
    flash('Account updated successfully.', 'success')
    return redirect(url_for('accounts.chart_of_accounts'))