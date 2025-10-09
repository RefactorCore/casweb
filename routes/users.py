from flask import Blueprint, request, flash, redirect, url_for
from models import db, User
from passlib.hash import pbkdf2_sha256
from flask_login import login_required, current_user
from .decorators import role_required

user_bp = Blueprint('users', __name__, url_prefix='/users')

@user_bp.route('/create', methods=['POST'])
@login_required
@role_required('Admin')
def create_user():
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')
    
    if User.query.filter_by(username=username).first():
        flash(f'Username "{username}" already exists.', 'danger')
        return redirect(url_for('core.settings'))

    if not username or not password or not role:
        flash('All fields are required.', 'danger')
        return redirect(url_for('core.settings'))

    new_user = User(
        username=username,
        password_hash=pbkdf2_sha256.hash(password),
        role=role
    )
    db.session.add(new_user)
    db.session.commit()
    flash(f'User "{username}" created successfully.', 'success')
    return redirect(url_for('core.settings'))


# --- ADD THIS NEW ROUTE FOR UPDATING USERS ---
@user_bp.route('/update/<int:user_id>', methods=['POST'])
@login_required
@role_required('Admin')
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get('password')
    role = request.form.get('role')

    # Update role
    user.role = role

    # If a new password was provided, hash and update it
    if new_password:
        user.password_hash = pbkdf2_sha256.hash(new_password)
    
    db.session.commit()
    flash(f'User "{user.username}" updated successfully.', 'success')
    return redirect(url_for('core.settings'))


# --- ADD THIS NEW ROUTE FOR DELETING USERS ---
@user_bp.route('/delete/<int:user_id>', methods=['POST'])
@login_required
@role_required('Admin')
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    # Safety check: prevent a user from deleting themselves
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('core.settings'))

    flash(f'User "{user.username}" has been deleted.', 'success')
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('core.settings'))