from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, ConsignmentSupplier, ConsignmentReceived, ConsignmentItem
from routes.decorators import role_required
from routes.utils import paginate_query, log_action
from datetime import datetime, timedelta

consignment_bp = Blueprint('consignment', __name__, url_prefix='/consignment')

# ============================================
# CONSIGNMENT SUPPLIERS
# ============================================

@consignment_bp.route('/suppliers')
@login_required
@role_required('Admin', 'Accountant')
def suppliers():
    """List all consignment suppliers"""
    search = request.args.get('search', '').strip()
    
    query = ConsignmentSupplier.query
    
    if search:
        query = query.filter(
            (ConsignmentSupplier.name.ilike(f'%{search}%')) |
            (ConsignmentSupplier.tin.ilike(f'%{search}%'))
        )
    
    query = query.order_by(ConsignmentSupplier.is_active.desc(), ConsignmentSupplier.name.asc())
    pagination = paginate_query(query, per_page=20)
    
    safe_args = {k: v for k, v in request.args.items() if k != 'page'}
    
    return render_template(
        'consignment/suppliers.html',
        suppliers=pagination.items,
        pagination=pagination,
        search=search,
        safe_args=safe_args
    )


@consignment_bp.route('/suppliers/add', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def add_supplier():
    """Add a new consignment supplier"""
    try:
        supplier = ConsignmentSupplier(
            name=request.form.get('name'),
            business_type=request.form.get('business_type'),
            tin=request.form.get('tin'),
            address=request.form.get('address'),
            contact_person=request.form.get('contact_person'),
            phone=request.form.get('phone'),
            email=request.form.get('email'),
            default_commission_rate=float(request.form.get('commission_rate', 15)),
            payment_terms_days=int(request.form.get('payment_terms_days', 30)),
            notes=request.form.get('notes')
        )
        
        db.session.add(supplier)
        db.session.commit()
        
        log_action(f'Added consignment supplier: {supplier.name}')
        flash(f'Supplier "{supplier.name}" added successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding supplier: {str(e)}', 'danger')
    
    return redirect(url_for('consignment.suppliers'))


@consignment_bp.route('/suppliers/<int:supplier_id>/edit', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def edit_supplier(supplier_id):
    """Edit an existing consignment supplier"""
    supplier = ConsignmentSupplier.query.get_or_404(supplier_id)
    
    try:
        supplier.name = request.form.get('name')
        supplier.business_type = request.form.get('business_type')
        supplier.tin = request.form.get('tin')
        supplier.address = request.form.get('address')
        supplier.contact_person = request.form.get('contact_person')
        supplier.phone = request.form.get('phone')
        supplier.email = request.form.get('email')
        supplier.default_commission_rate = float(request.form.get('commission_rate', 15))
        supplier.payment_terms_days = int(request.form.get('payment_terms_days', 30))
        supplier.notes = request.form.get('notes')
        
        db.session.commit()
        
        log_action(f'Updated consignment supplier: {supplier.name}')
        flash(f'Supplier "{supplier.name}" updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating supplier: {str(e)}', 'danger')
    
    return redirect(url_for('consignment.suppliers'))


@consignment_bp.route('/suppliers/<int:supplier_id>/toggle', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def toggle_supplier(supplier_id):
    """Toggle supplier active status"""
    supplier = ConsignmentSupplier.query.get_or_404(supplier_id)
    
    supplier.is_active = not supplier.is_active
    db.session.commit()
    
    status = "activated" if supplier.is_active else "deactivated"
    log_action(f'{status.capitalize()} consignment supplier: {supplier.name}')
    flash(f'Supplier "{supplier.name}" {status}!', 'success')
    
    return redirect(url_for('consignment.suppliers'))


# ============================================
# RECEIVE CONSIGNMENT
# ============================================

@consignment_bp.route('/receive', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant', 'Cashier')
def receive():
    """Receive new consignment goods"""
    if request.method == 'POST':
        try:
            supplier_id = int(request.form.get('supplier_id'))
            commission_rate = float(request.form.get('commission_rate', 15))
            expected_return_days = request.form.get('expected_return_days')
            notes = request.form.get('notes')
            items_json = request.form.get('items_json')
            
            # Parse items
            import json
            items = json.loads(items_json) if items_json else []
            
            if not items:
                flash('Please add at least one item to the consignment.', 'warning')
                return redirect(url_for('consignment.receive'))
            
            # Get supplier
            supplier = ConsignmentSupplier.query.get_or_404(supplier_id)
            
            # Generate receipt number
            from models import CompanyProfile
            profile = CompanyProfile.query.first()
            
            if not hasattr(profile, 'next_consignment_number') or profile.next_consignment_number is None:
                profile.next_consignment_number = 1
            
            receipt_num = profile.next_consignment_number
            profile.next_consignment_number += 1
            receipt_number = f"CONS-{receipt_num:06d}"
            
            # Calculate expected return date
            expected_return_date = None
            if expected_return_days:
                expected_return_date = datetime.utcnow() + timedelta(days=int(expected_return_days))
            
            # Create consignment
            consignment = ConsignmentReceived(
                receipt_number=receipt_number,
                supplier_id=supplier_id,
                date_received=datetime.utcnow(),
                expected_return_date=expected_return_date,
                commission_rate=commission_rate,
                notes=notes,
                created_by_id=current_user.id
            )
            
            db.session.add(consignment)
            db.session.flush()
            
            # Add items
            total_items = 0
            total_value = 0.0
            
            for item_data in items:
                qty = int(item_data.get('quantity', 0))
                price = float(item_data.get('retail_price', 0))
                
                if qty <= 0 or price <= 0:
                    continue
                
                item = ConsignmentItem(
                    consignment_id=consignment.id,
                    sku=item_data.get('sku'),
                    product_name=item_data.get('name'),
                    description=item_data.get('description'),
                    barcode=item_data.get('barcode'),
                    quantity_received=qty,
                    retail_price=price
                )
                
                db.session.add(item)
                
                total_items += qty
                total_value += (qty * price)
            
            # Update consignment totals
            consignment.total_items = total_items
            consignment.total_value = total_value
            
            db.session.commit()
            
            log_action(f'Received consignment {receipt_number} from {supplier.name} - {total_items} items, ₱{total_value:,.2f}')
            flash(f'✅ Consignment {receipt_number} received successfully!', 'success')
            
            return redirect(url_for('consignment.view_consignment', consignment_id=consignment.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'❌ Error receiving consignment: {str(e)}', 'danger')
            return redirect(url_for('consignment.receive'))
    
    # GET request - show form
    suppliers = ConsignmentSupplier.query.filter_by(is_active=True).order_by(ConsignmentSupplier.name).all()
    return render_template('consignment/receive.html', suppliers=suppliers)


# ============================================
# LIST & VIEW CONSIGNMENTS
# ============================================

@consignment_bp.route('/list')
@login_required
@role_required('Admin', 'Accountant', 'Cashier')
def list_received():
    """List all received consignments"""
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', 'all')
    
    query = ConsignmentReceived.query
    
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    if search:
        query = query.join(ConsignmentSupplier).filter(
            (ConsignmentReceived.receipt_number.ilike(f'%{search}%')) |
            (ConsignmentSupplier.name.ilike(f'%{search}%'))
        )
    
    query = query.order_by(ConsignmentReceived.date_received.desc())
    pagination = paginate_query(query, per_page=20)
    
    safe_args = {k: v for k, v in request.args.items() if k != 'page'}
    
    return render_template(
        'consignment/list.html',
        consignments=pagination.items,
        pagination=pagination,
        search=search,
        status_filter=status_filter,
        safe_args=safe_args
    )


@consignment_bp.route('/view/<int:consignment_id>')
@login_required
@role_required('Admin', 'Accountant', 'Cashier')
def view_consignment(consignment_id):
    """View detailed consignment information"""
    consignment = ConsignmentReceived.query.get_or_404(consignment_id)
    items = ConsignmentItem.query.filter_by(consignment_id=consignment_id).all()
    
    return render_template(
        'consignment/view.html',
        consignment=consignment,
        items=items
    )