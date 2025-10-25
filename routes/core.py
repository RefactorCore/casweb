from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response, session
from models import db, User, Product, Purchase, PurchaseItem, Sale, SaleItem, JournalEntry, StockAdjustment, Account, Supplier
import json
from config import Config
from datetime import datetime, timedelta
from sqlalchemy import func, exc 
from models import Sale, CompanyProfile, User, AuditLog
import io, csv, json
from io import StringIO
from routes.utils import paginate_query
from passlib.hash import pbkdf2_sha256
from flask_login import login_user, logout_user, login_required, current_user
from routes.decorators import role_required
from .utils import log_action
from extensions import limiter

core_bp = Blueprint('core', __name__)
VAT_RATE = Config.VAT_RATE


@core_bp.route('/setup/license', methods=['GET', 'POST'])
def setup_license():
    if request.method == 'POST':
        license_key = request.form.get('license_key')
        if license_key == 'test123':
            session['validated_license_key'] = license_key
            return redirect(url_for('core.setup_company'))
        else:
            flash('Invalid license key. Please use the testing key.', 'danger')
    return render_template('setup/license.html')


@core_bp.route('/setup/company', methods=['GET', 'POST'])
def setup_company():
    if request.method == 'POST':
        name = request.form.get('name')
        tin = request.form.get('tin')
        address = request.form.get('address')
        style = request.form.get('business_style')

        if not name or not tin or not address:
            flash('Please fill out all company details.', 'warning')
            return redirect(url_for('core.setup_company'))

        license_key = session.pop('validated_license_key', None)

        profile = CompanyProfile(name=name, tin=tin, address=address, business_style=style)
        db.session.add(profile)
        db.session.commit()
        return redirect(url_for('core.setup_admin'))
    return render_template('setup/company.html')


@core_bp.route('/setup/admin', methods=['GET', 'POST'])
def setup_admin():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Username and password cannot be empty.', 'warning')
            return redirect(url_for('core.setup_admin'))

        hashed_password = pbkdf2_sha256.hash(password)
        admin_user = User(username=username, password_hash=hashed_password, role='Admin')
        db.session.add(admin_user)
        db.session.commit()

        # Log the new admin in automatically
        login_user(admin_user)
        flash('Setup complete! Welcome to your new accounting system.', 'success')
        return redirect(url_for('core.index'))
    return render_template('setup/admin.html')
    
@core_bp.route('/')
@login_required
def index():
    # --- Base data ---
    products = Product.query.all()
    low_stock = [p for p in products if p.quantity <= 5 and p.is_active]

    # --- NEW: Filter inventory value to only include active products ---
    active_products = [p for p in products if p.is_active]
    total_inventory_value = sum(p.cost_price * p.quantity for p in active_products)
    products_in_stock = Product.query.filter(Product.quantity > 0, Product.is_active == True).count()
    # --- END OF NEW ---

    # --- Get period filter (default to '7' days) ---
    period = request.args.get('period', '7')
    today = datetime.utcnow()
    start_date = None
    current_filter_label = ''

    # --- Set start_date and label based on the period ---
    if period == '12':
        start_date = today - timedelta(hours=12)
        current_filter_label = 'Last 12 Hours'
    elif period == '30':
        start_date = today - timedelta(days=30)
        current_filter_label = 'Last 30 Days'
    elif period == 'all':
        current_filter_label = 'All Time'
        # start_date remains None, so queries won't be filtered
    else:
        # Default to 7 Days
        period = '7' # Explicitly set for the button highlighting
        start_date = today - timedelta(days=7)
        current_filter_label = 'Last 7 Days'

    # --- Build FILTERED queries for Sales and Purchases ---
    sales_query = db.session.query(func.sum(Sale.total))
    purchases_query = db.session.query(func.sum(Purchase.total))

    if start_date:
        sales_query = sales_query.filter(Sale.created_at >= start_date)
        purchases_query = purchases_query.filter(Purchase.created_at >= start_date)

    # --- Execute FILTERED queries ---
    total_sales = sales_query.scalar() or 0
    total_purchases = purchases_query.scalar() or 0
    net_income = total_sales - total_purchases # This is now also filtered

    # --- Charting Logic ---
    sales_by_period = []
    labels = []
    
    if period == '12':
        intervals = [today - timedelta(hours=i) for i in range(11, -1, -1)]
        for hour_start in intervals:
            hour_end = hour_start + timedelta(hours=1)
            hour_total = (db.session.query(func.sum(Sale.total)).filter(Sale.created_at >= hour_start).filter(Sale.created_at < hour_end).scalar() or 0)
            sales_by_period.append(hour_total)
            labels.append(hour_start.strftime('%I%p'))
    else:
        if period == '30':
            days = 30
        elif period == '7':
            days = 7
        else: # 'all'
            # For 'All Time', let's default to a 90-day chart view so it's not overwhelming
            days = 90
            
        today_date = datetime.utcnow().date()
        last_n_days = [today_date - timedelta(days=i) for i in range(days - 1, -1, -1)]
        for day in last_n_days:
            day_total = (db.session.query(func.sum(Sale.total)).filter(func.date(Sale.created_at) == day).scalar() or 0)
            sales_by_period.append(day_total)
            labels.append(day.strftime('%b %d'))
            
    # --- Top Sellers (This is still all-time, which is usually OK) ---
    top_sellers = (
        db.session.query(
            Product.name,
            func.sum(SaleItem.qty).label('total_qty_sold')
        )
        .join(SaleItem, Product.id == SaleItem.product_id)
        .group_by(Product.name)
        .order_by(func.sum(SaleItem.qty).desc())
        .limit(10)
        .all()
    )

    return render_template(
        'index.html',
        products=products,
        low_stock=low_stock,
        # --- Pass the new DYNAMIC totals ---
        total_sales=total_sales,
        total_purchases=total_purchases,
        net_income=net_income,
        # --- Pass the STATIC (all-time) inventory values ---
        total_inventory_value=total_inventory_value,
        products_in_stock=products_in_stock,
        # --- Pass the filter and chart data ---
        labels=labels,
        sales_by_day=sales_by_period,
        top_sellers=top_sellers,
        current_period_filter=period,
        current_filter_label=current_filter_label
    )

@login_required
@core_bp.route('/inventory', methods=['GET', 'POST'])
def inventory():
    from models import Product

    # Handle product form submission (keeping this code block as is)
    if request.method == 'POST':
        data = request.form
        new_prod = Product(
            sku=data.get('sku'),
            name=data.get('name'),
            sale_price=float(data.get('sale_price') or 0),
            cost_price=float(data.get('cost_price') or 0),
            quantity=int(data.get('quantity') or 0)
        )
        db.session.add(new_prod)
        db.session.commit()
        flash('Product added successfully.', 'success')
        return redirect(url_for('core.inventory'))

    # --- Handle GET (view with pagination) ---
    search = request.args.get('search', '').strip()
    query = Product.query

    if search:
        query = query.filter(
            (Product.name.ilike(f"%{search}%")) |
            (Product.sku.ilike(f"%{search}%"))
        )

    # Order and paginate
    query = query.order_by(Product.is_active.desc(), Product.name.asc())
    pagination = paginate_query(query, per_page=12) # Using 2 for testing

    # 👇 NEW: Create a dictionary of current arguments, excluding 'page'
    # This is the fix for the pagination link error.
    safe_args = {k: v for k, v in request.args.items() if k != 'page'}

    all_active_products = Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()


    has_opening_balance = db.session.query(JournalEntry.id)\
        .filter(JournalEntry.entries_json.ilike('%"account_code": "302"%'))\
        .first() is not None
    
    return render_template(
        'inventory.html',
        products=pagination.items,
        pagination=pagination,
        search=search,
        # 👇 NEW: Pass the filtered arguments dictionary
        safe_args=safe_args,
        all_active_products=all_active_products,
        has_opening_balance=has_opening_balance
    )

# ✅ Update product
@core_bp.route('/update_product', methods=['POST'])
def update_product():
    sku = request.form.get('sku')
    product = Product.query.filter_by(sku=sku).first()
    if not product:
        flash('Product not found.', 'danger')
        return redirect(request.referrer or url_for('core.inventory'))

    # Update fields
    product.name = request.form.get('name')
    product.sale_price = float(request.form.get('sale_price') or 0)
    product.cost_price = float(request.form.get('cost_price') or 0)
    # product.quantity = int(request.form.get('quantity') or 0)

    log_action(f'Updated product SKU: {product.sku}, Name: {product.name}.')
    db.session.commit()
    flash(f'Product {product.sku} updated successfully.', 'success')
    return redirect(request.referrer or url_for('core.inventory'))


@core_bp.route('/product/toggle-status/<int:product_id>', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def toggle_product_status(product_id):
    product = Product.query.get_or_404(product_id)
    
    # Toggle the status
    product.is_active = not product.is_active
    
    if product.is_active:
        log_action(f'Enabled product: {product.sku} ({product.name}).')
        flash(f'Product {product.name} has been enabled.', 'success')
    else:
        log_action(f'Disabled product: {product.sku} ({product.name}).')
        flash(f'Product {product.name} has been disabled.', 'danger')
        
    db.session.commit()
    return jsonify({'status': 'ok', 'new_is_active': product.is_active})


# --- ADD THIS ENTIRE FUNCTION ---

@core_bp.route('/inventory/bulk-add', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant')
def inventory_bulk_add():
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file part', 'danger')
            return redirect(request.url)
        
        file = request.files['csv_file']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(request.url)

        if not file.filename.endswith('.csv'):
            flash('Invalid file type. Please upload a .csv file.', 'danger')
            return redirect(request.url)

        try:
            stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
            csv_reader = csv.reader(stream)
            
            # Skip header row
            next(csv_reader, None)
            
            products_added = 0
            total_value = 0.0
            errors = []
            
            for row in csv_reader:
                if not row or len(row) < 5:
                    continue # Skip empty/invalid rows

                try:
                    sku = row[0].strip()
                    name = row[1].strip()
                    sale_price = float(row[2] or 0.0)
                    cost_price = float(row[3] or 0.0)
                    quantity = int(row[4] or 0)
                    
                    if not sku or not name:
                        errors.append(f"Skipped row (missing SKU or Name): {','.join(row)}")
                        continue

                    # Check for duplicate SKU
                    existing_sku = Product.query.filter_by(sku=sku).first()
                    if existing_sku:
                        errors.append(f"SKU '{sku}' already exists. Skipped.")
                        continue

                    new_prod = Product(
                        sku=sku,
                        name=name,
                        sale_price=sale_price,
                        cost_price=cost_price,
                        quantity=quantity
                    )
                    db.session.add(new_prod)
                    db.session.flush()

                    if quantity > 0 and cost_price > 0:
                        initial_value = round(quantity * cost_price, 2)
                        total_value += initial_value
                        
                        # 120: Inventory, 302: Opening Balance Equity
                        je_lines = [
                            {'account_code': '120', 'debit': initial_value, 'credit': 0},
                            {'account_code': '302', 'debit': 0, 'credit': initial_value}
                        ]
                        je = JournalEntry(
                            description=f'Beginning Balance for {new_prod.sku} ({new_prod.name})',
                            entries_json=json.dumps(je_lines)
                        )
                        db.session.add(je)
                    
                    products_added += 1

                except ValueError:
                    db.session.rollback()
                    errors.append(f"Invalid number format for row: {','.join(row)}. Skipped.")
                except Exception as e:
                    db.session.rollback()
                    errors.append(f"Error on row {','.join(row)}: {str(e)}. Skipped.")

            # --- Commit all good entries at the end ---
            db.session.commit()
            
            flash(f'Successfully added {products_added} products.', 'success')
            if total_value > 0:
                flash(f'Recorded ₱{total_value:,.2f} in Beginning Inventory Value.', 'info')
            if errors:
                flash('Some rows were not imported:', 'warning')
                for error in errors:
                    flash(error, 'danger')
            
            log_action(f'Bulk-added {products_added} products with total beginning value of {total_value}.')
            return redirect(url_for('core.inventory'))

        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred processing the file: {str(e)}', 'danger')
            return redirect(request.url)

    # GET request just shows the template
    # We must create this template file.
    return render_template('inventory_bulk_add.html')


@core_bp.route('/api/add_multiple_products', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant') # Good to protect this
def api_add_multiple_products():
    data = request.json
    products_data = data.get('products', [])
    
    if not products_data:
        return jsonify({'error': 'No product data provided'}), 400

    new_product_count = 0
    total_value = 0.0
    errors = []
    
    try:
        for p_data in products_data:
            # Basic validation
            sku = p_data.get('sku')
            name = p_data.get('name')
            
            if not sku or not name:
                errors.append(f"Skipped row (missing SKU or Name): {sku}")
                continue
            
            # Check if SKU already exists
            if Product.query.filter_by(sku=sku).first():
                 errors.append(f"SKU '{sku}' already exists. Skipped.")
                 continue 
            
            try:
                # Get data from payload
                initial_cost = float(p_data.get('cost_price') or 0)
                initial_qty = int(p_data.get('quantity') or 0)

                new_prod = Product(
                    sku=sku,
                    name=name,
                    sale_price=float(p_data.get('sale_price') or 0),
                    cost_price=initial_cost,
                    quantity=initial_qty
                )
                db.session.add(new_prod)
                
                # --- NEW: Create Beginning Balance Journal Entry ---
                if initial_qty > 0 and initial_cost > 0:
                    initial_value = round(initial_qty * initial_cost, 2)
                    total_value += initial_value
                    
                    # 120: Inventory, 302: Opening Balance Equity
                    je_lines = [
                        {'account_code': '120', 'debit': initial_value, 'credit': 0},
                        {'account_code': '302', 'debit': 0, 'credit': initial_value}
                    ]
                    je = JournalEntry(
                        description=f'Beginning Balance for {new_prod.sku} ({new_prod.name})',
                        entries_json=json.dumps(je_lines)
                    )
                    db.session.add(je)
                # --- END OF NEW BLOCK ---
                
                new_product_count += 1
            
            except ValueError:
                errors.append(f"Invalid number for SKU '{sku}'. Skipped.")
            
        db.session.commit()
        
        log_action(f'Bulk-added {new_product_count} products with total beginning value of {total_value}.')
        
        # Check if there were errors to report
        if errors:
             return jsonify({
                'status': 'partial', 
                'count': new_product_count,
                'error': f'Added {new_product_count} products, but some failed. See errors.',
                'errors': errors
            }), 207 # 207 Multi-Status
            
        return jsonify({'status': 'ok', 'count': new_product_count})

    except exc.IntegrityError as e:
        db.session.rollback()
        return jsonify({'error': 'A database error occurred (e.g., duplicate SKU).', 'details': str(e)}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@login_required
@core_bp.route('/purchase', methods=['GET', 'POST'])
@role_required('Admin', 'Accountant', 'Cashier')
def purchase():
    if request.method == 'POST':
        try:
            supplier_name = request.form.get('supplier', '').strip() or 'Unknown'
            items_raw = request.form.get('items_json')
            items = json.loads(items_raw) if items_raw else []

            if not items:
                flash("No items added to the purchase. Please add products first.", "warning")
                return redirect(url_for('core.purchase'))

            # --- NEW: Find or Create Supplier ---
            # This makes your supplier list grow automatically
            supplier = Supplier.query.filter_by(name=supplier_name).first()
            if not supplier and supplier_name != 'Unknown':
                supplier = Supplier(name=supplier_name)
                db.session.add(supplier)
                db.session.flush()

            # --- Create Purchase record ---
            purchase = Purchase(total=0, vat=0, supplier=supplier_name)
            db.session.add(purchase)
            db.session.flush()

            total, vat_total = 0.0, 0.0

            for item in items:
                sku = item.get('sku')
                qty = int(item.get('qty', 0))
                unit_cost = float(item.get('unit_cost', 0))
                name = item.get('name', 'Unnamed')

                if not sku or qty <= 0 or unit_cost <= 0:
                    continue  # Skip invalid item rows

                # --- Compute line totals ---
                line_net = qty * unit_cost
                vat = round(line_net * VAT_RATE, 2)
                line_total = line_net + vat

                # --- Find or create product ---
                product = Product.query.filter_by(sku=sku).first()
                if not product:
                    product = Product(
                        sku=sku,
                        name=name,
                        sale_price=round(unit_cost * 1.5, 2),  # 50% markup default
                        cost_price=unit_cost,
                        quantity=qty,
                        is_active=True
                    )
                    db.session.add(product)
                    db.session.flush()
                else:
                    # Weighted average cost update
                    # Make sure product quantity is not zero to avoid DivisionByZero
                    if product.quantity + qty > 0:
                        old_val = product.cost_price * product.quantity
                        new_val = unit_cost * qty
                        product.quantity += qty
                        product.cost_price = (old_val + new_val) / product.quantity
                    else:
                        product.quantity += qty # This handles 0 case
                        product.cost_price = unit_cost # Just update to new cost

                # --- Record PurchaseItem ---
                purchase_item = PurchaseItem(
                    purchase_id=purchase.id,
                    product_id=product.id,
                    product_name=product.name,
                    sku=sku,
                    qty=qty,
                    unit_cost=unit_cost,
                    line_total=line_total
                )
                db.session.add(purchase_item)

                total += line_total
                vat_total += vat

            # --- Finalize totals ---
            purchase.total = total
            purchase.vat = vat_total

            # --- Record journal entry ---
            journal_lines = [
                {"account_code": "120", "debit": total - vat_total, "credit": 0}, # 120: Inventory
                {"account_code": "602", "debit": vat_total, "credit": 0},         # 602: VAT Input
                {"account_code": "201", "debit": 0, "credit": total}              # 201: Accounts Payable
            ]
            journal = JournalEntry(
                description=f"Purchase #{purchase.id} - {supplier_name}",
                entries_json=json.dumps(journal_lines)
            )
            db.session.add(journal)
            log_action(f'Recorded Purchase #{purchase.id} from {supplier_name} for ₱{total:,.2f}.')
            db.session.commit()

            flash(f"✅ Purchase #{purchase.id} recorded successfully.", "success")
            return redirect(url_for('core.purchases'))

        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error saving purchase: {str(e)}", "danger")
            return redirect(url_for('core.purchase'))

    products = Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()
    suppliers = Supplier.query.order_by(Supplier.name).all() # Get all suppliers
    today = datetime.utcnow().strftime('%Y-%m-%d') # Get today's date
    
    return render_template('purchase.html', products=products, suppliers=suppliers, today=today)



# ✅ New: List all purchases
@core_bp.route('/purchases')
def purchases():
    purchases = Purchase.query.order_by(Purchase.id.desc()).all()
    return render_template('purchases.html', purchases=purchases)


# @core_bp.route('/delete_purchase/<int:purchase_id>', methods=['POST'])
# def delete_purchase(purchase_id):
#     purchase = Purchase.query.get_or_404(purchase_id)
#     log_action(f'Deleted Purchase #{purchase.id} (Supplier: {purchase.supplier}, Total: ₱{purchase.total:,.2f}).')
#     db.session.delete(purchase)
#     db.session.commit()
#     return jsonify({'status': 'deleted'})
@core_bp.route('/purchase/cancel/<int:purchase_id>', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant') # Protect this action
def cancel_purchase(purchase_id):
    """
    Cancels a purchase by creating a reversing journal entry.
    This does NOT delete the record, ensuring compliance.
    """
    purchase = Purchase.query.get_or_404(purchase_id)

    # 1. Check if already canceled
    if purchase.status == 'Canceled':
        flash(f'Purchase #{purchase.id} is already canceled.', 'warning')
        return redirect(url_for('core.purchases'))

    try:
        # 2. Calculate reversal amounts
        total_net = purchase.total - purchase.vat
        total_vat = purchase.vat
        total = purchase.total

        # 3. Create the reversing journal entry
        # Original JE was:
        #   Debit:   Inventory (120) [net]
        #   Debit:   VAT Input (602) [vat]
        #   Credit:  Accounts Payable (201) [total]
        #
        # Reversing JE is:
        #   Debit:   Accounts Payable (201) [total]
        #   Credit:  Inventory (120) [net]
        #   Credit:  VAT Input (602) [vat]
        
        journal_lines = [
            {"account_code": "201", "debit": total, "credit": 0},         # 201: Accounts Payable
            {"account_code": "120", "debit": 0, "credit": total_net},     # 120: Inventory
            {"account_code": "602", "debit": 0, "credit": total_vat}      # 602: VAT Input
        ]
        journal = JournalEntry(
            description=f"Reversal/Cancel of Purchase #{purchase.id} - {purchase.supplier}",
            entries_json=json.dumps(journal_lines)
        )
        db.session.add(journal)

        # 4. Update the purchase status
        purchase.status = 'Canceled'
        
        # 5. Log this compliant action
        log_action(f'Canceled Purchase #{purchase.id} (Supplier: {purchase.supplier}, Total: ₱{purchase.total:,.2f}). Reversing JE created.')
        
        db.session.commit()
        flash(f'Purchase #{purchase.id} has been canceled and a reversing journal entry was posted.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error canceling purchase: {str(e)}', 'danger')

    return redirect(url_for('core.purchases'))


# ✅ New: View a specific purchase
@core_bp.route('/purchase/<int:purchase_id>')
def view_purchase(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    items = PurchaseItem.query.filter_by(purchase_id=purchase.id).all()
    return render_template('purchase_view.html', purchase=purchase, items=items)


@core_bp.route('/pos')
@login_required
@role_required('Admin', 'Cashier')
def pos():
    # --- Handle GET (view with pagination and search) ---
    search = request.args.get('search', '').strip()
    query = Product.query.filter_by(is_active=True)

    if search:
        query = query.filter(
            (Product.name.ilike(f"%{search}%")) |
            (Product.sku.ilike(f"%{search}%"))
        )

    # Order and paginate (you can adjust per_page)
    query = query.order_by(Product.name.asc())
    pagination = paginate_query(query, per_page=12) 

    safe_args = {k: v for k, v in request.args.items() if k != 'page'}

    return render_template(
        'pos.html',
        products=pagination.items,
        pagination=pagination,
        search=search,
        safe_args=safe_args
    )


@core_bp.route('/api/sale', methods=['POST'])
def api_sale():
    data = request.json
    items = data.get('items', [])
    doc_type = data.get('doc_type', 'OR') # Assumes frontend will send this, defaults to 'OR'

    if not items:
        return jsonify({'error': 'No items in sale'}), 400

    try:
        # --- NEW: Get the next document number ---
        profile = CompanyProfile.query.first()
        if not profile:
            # Important: You must have a company profile in your database for this to work
            return jsonify({'error': 'Company profile not set up in settings'}), 500

        if doc_type == 'SI':
            doc_num = profile.next_si_number
            profile.next_si_number += 1 # Increment for the next sale
            full_doc_number = f"SI-{doc_num:06d}" # Formats to SI-000001
        else: # Default to OR
            doc_num = profile.next_or_number
            profile.next_or_number += 1 # Increment for the next sale
            full_doc_number = f"OR-{doc_num:06d}" # Formats to OR-000001
        
        # --- MODIFIED: Pass the new document info when creating the Sale ---
        sale = Sale(total=0, vat=0, document_number=full_doc_number, document_type=doc_type)
        db.session.add(sale)
        db.session.flush()

        total = vat_total = cogs_total = 0
        for it in items:
            sku = it['sku']
            qty = int(it['qty'])
            product = Product.query.filter_by(sku=sku).first()
            if not product:
                # Rollback to prevent leaving an empty sale record
                db.session.rollback()
                return jsonify({'error': f'Product {sku} not found'}), 404
            if product.quantity < qty:
                db.session.rollback()
                return jsonify({'error': f'Insufficient stock for {product.name}'}), 400

            line_total = qty * product.sale_price 
            line_net = round(line_total / (1 + VAT_RATE), 2)
            vat = round(line_total - line_net, 2)
            cogs = qty * product.cost_price

            db.session.add(SaleItem(
                sale_id=sale.id, product_id=product.id,
                product_name=product.name, sku=sku,
                qty=qty, 
                unit_price=product.sale_price, # Store the inclusive price
                line_total=line_total, # Store the inclusive total
                cogs=cogs
            ))

            product.quantity -= qty
            total += line_total
            vat_total += vat
            cogs_total += cogs

        sale.total, sale.vat = total, vat_total

        je_lines = [
            {'account_code': '101', 'debit': total, 'credit': 0},                   # 101: Cash
            {'account_code': '401', 'debit': 0, 'credit': total - vat_total},      # 401: Sales Revenue
            {'account_code': '601', 'debit': 0, 'credit': vat_total},              # 601: VAT Payable
            {'account_code': '501', 'debit': cogs_total, 'credit': 0},              # 501: COGS
            {'account_code': '120', 'debit': 0, 'credit': cogs_total},              # 120: Inventory
        ]
        # --- MODIFIED: Update Journal Entry description ---
        db.session.add(JournalEntry(description=f'Sale #{sale.id} ({full_doc_number})', entries_json=json.dumps(je_lines)))

        log_action(f'Recorded Sale #{sale.id} ({full_doc_number}) for ₱{total:,.2f}.')
        
        db.session.commit()
        
        # --- MODIFIED: Return the new document number to the frontend ---
        return jsonify({
            'status': 'ok', 
            'sale_id': sale.id, 
            'receipt_number': full_doc_number,
            'vat': vat_total  # <-- ADD THIS LINE
        })

    # --- NEW: Error handling for database issues ---
    except exc.IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Failed to generate unique document number. Please try again.'}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@core_bp.route('/sales')
def sales():
    from models import Sale
    from app import db  # make sure db is imported

    search = request.args.get('search', '').strip()
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    page = request.args.get('page', 1, type=int)

    query = Sale.query

    if search:
        query = query.filter(
            (Sale.customer_name.ilike(f"%{search}%")) |
            (Sale.id.cast(db.String).ilike(f"%{search}%"))
        )
    if start_date:
        query = query.filter(Sale.created_at >= start_date)
    if end_date:
        query = query.filter(Sale.created_at <= end_date)

    pagination = query.order_by(Sale.created_at.desc()).paginate(page=page, per_page=20, error_out=False)

    sales = pagination.items

    # Compute summary for current page (not full dataset)
    total_sales = sum(s.total for s in sales)
    total_vat = sum(s.vat for s in sales)
    summary = {
        "total_sales": total_sales,
        "total_vat": total_vat,
        "count": len(sales)
    } if sales else None

    return render_template(
        'sales.html',
        sales=sales,
        summary=summary,
        pagination=pagination,
        search=search,
        start_date=start_date,
        end_date=end_date
    )



@core_bp.route('/sales/<int:sale_id>/print')
def print_receipt(sale_id):
    from models import Sale, SaleItem
    sale = Sale.query.get_or_404(sale_id)
    items = SaleItem.query.filter_by(sale_id=sale.id).all()
    return render_template('receipt.html', sale=sale, items=items)

@core_bp.route('/export_sales')
def export_sales():
    format_type = request.args.get('format', 'csv')
    
    # 🔍 Optional filters (same as in your sales page)
    search = request.args.get('search', '').strip()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = Sale.query

    if search:
        query = query.filter(Sale.customer.ilike(f"%{search}%") | Sale.id.ilike(f"%{search}%"))
    if start_date:
        query = query.filter(Sale.created_at >= start_date)
    if end_date:
        query = query.filter(Sale.created_at <= end_date)

    sales = query.order_by(Sale.created_at.desc()).all()

    if format_type == 'csv':
        # 🧾 Generate CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Customer", "Total", "VAT", "Status", "Date"])

        for s in sales:
            writer.writerow([
                s.id,
                s.customer_name or '',
                f"{s.total:.2f}",
                f"{s.vat:.2f}",
                s.status or "",
                s.created_at.strftime('%Y-%m-%d %H:%M') if s.created_at else ""
            ])

        output.seek(0)
        filename = f"sales_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    # Default fallback
    return redirect(url_for('core.sales'))


@core_bp.route('/sales/<int:sale_id>')
def view_sale(sale_id):
    from models import SaleItem, Sale  # adjust imports per your structure

    sale = Sale.query.get_or_404(sale_id)
    items = SaleItem.query.filter_by(sale_id=sale.id).all()

    return render_template('view_sale.html', sale=sale, items=items)

@core_bp.route('/journal-entries')
@role_required('Admin', 'Accountant')
def journal_entries():
    """Display all journal entries with search and date filters."""
    search = request.args.get('search', '')
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    query = JournalEntry.query.order_by(JournalEntry.created_at.desc())

    # --- THIS IS NEW: Create a map of Account Codes -> Account Names ---
    # We will pass this to the template to display names instead of codes
    accounts_map = {a.code: a.name for a in Account.query.all()}
    
    # --- UPDATED: Search/Filter Logic ---
    safe_args = {}
    if search:
        safe_args['search'] = search
        # Updated to search for account_code instead of account name
        query = query.filter(
            (JournalEntry.description.ilike(f'%{search}%')) |
            (JournalEntry.entries_json.ilike(f'%\"account_code\": \"{search}\"%')) 
        )
        
    start_date, end_date = None, None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(JournalEntry.created_at >= start_date)
            safe_args['start_date'] = start_date_str
        except ValueError:
            pass # ignore invalid date
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            # Add 1 day to end_date to make it inclusive
            end_date = end_date + timedelta(days=1)
            query = query.filter(JournalEntry.created_at <= end_date)
            safe_args['end_date'] = end_date_str
        except ValueError:
            pass # ignore invalid date
    # --- END OF UPDATED LOGIC ---

    pagination = paginate_query(query)
    
    return render_template(
        'reports.html',
        journals=pagination.items,
        pagination=pagination,
        accounts_map=accounts_map,  # <-- Pass the map to the template
        safe_args=safe_args,
        start_date=start_date_str,
        end_date=end_date_str
    )

@core_bp.route('/export/journal-entries')
@login_required
@role_required('Admin', 'Accountant')
def export_journal_entries():
    """Export journal entries to CSV, respecting filters."""
    search = request.args.get('search', '')
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    query = JournalEntry.query.order_by(JournalEntry.created_at.asc())
    
    # --- Create the account map just like in the main function ---
    accounts_map = {a.code: a.name for a in Account.query.all()}

    # --- Apply filters just like in the main function ---
    if search:
        query = query.filter(
            (JournalEntry.description.ilike(f'%{search}%')) |
            (JournalEntry.entries_json.ilike(f'%\"account_code\": \"{search}\"%'))
        )
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(JournalEntry.created_at >= start_date)
        except ValueError: pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(JournalEntry.created_at <= end_date)
        except ValueError: pass

    journals = query.all()
    
    # --- Create CSV ---
    si = io.StringIO()
    writer = csv.writer(si)
    
    # Write Header
    writer.writerow(['Journal_ID', 'Date', 'Description', 'Account_Code', 'Account_Name', 'Debit', 'Credit'])
    
    # Write Rows
    for je in journals:
        je_date = je.created_at.strftime('%Y-%m-%d %H:%M')
        for line in je.entries():
            code = line.get('account_code')
            # Use the map to find the name
            name = accounts_map.get(code, code) # Fallback to code if not found
            debit = line.get('debit', 0)
            credit = line.get('credit', 0)
            writer.writerow([je.id, je_date, je.description, code, name, debit, credit])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=journal_entries.csv"}
    )

@core_bp.route('/export_journals')
def export_journals():
    """Export journal entries as CSV."""
    journals = JournalEntry.query.order_by(JournalEntry.created_at.desc()).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Description", "Account", "Debit", "Credit", "Date"])

    for j in journals:
        for e in j.entries():
            writer.writerow([
                j.id,
                j.description or '',
                e.get("account", ""),
                e.get("debit", 0),
                e.get("credit", 0),
                j.created_at.strftime("%Y-%m-%d %H:%M") if j.created_at else "",
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=journal_entries.csv"}
    )

@core_bp.route('/new_journal')
def new_journal():
    return "📝 Journal entry creation page (coming soon)"


@core_bp.route('/vat_report')
def vat_report():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query_sales = Sale.query
    query_purchases = Purchase.query

    if start_date and end_date:
        query_sales = query_sales.filter(Sale.created_at.between(start_date, end_date))
        query_purchases = query_purchases.filter(Purchase.created_at.between(start_date, end_date))

    sale_vat = sum(s.vat for s in query_sales.all())
    purchase_vat = sum(p.vat for p in query_purchases.all())
    vat_payable = sale_vat - purchase_vat

    return render_template(
        'vat_report.html',
        sale_vat=sale_vat,
        purchase_vat=purchase_vat,
        vat_payable=vat_payable,
        start_date=start_date,
        end_date=end_date
    )


def parse_date(date_str):
    """Helper to safely parse YYYY-MM-DD format strings."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None

@core_bp.route('/export_vat', methods=['GET'])
def export_vat_report():
    # --- Parse optional date filters ---
    start_date = parse_date(request.args.get("start_date"))
    end_date = parse_date(request.args.get("end_date"))

    # --- Build queries ---
    sale_query = Sale.query
    purchase_query = Purchase.query

    if start_date:
        sale_query = sale_query.filter(Sale.created_at >= start_date)
        purchase_query = purchase_query.filter(Purchase.created_at >= start_date)
    if end_date:
        sale_query = sale_query.filter(Sale.created_at <= end_date)
        purchase_query = purchase_query.filter(Purchase.created_at <= end_date)

    # --- Compute VAT totals ---
    sale_vat = sum(s.vat or 0 for s in sale_query.all())
    purchase_vat = sum(p.vat or 0 for p in purchase_query.all())
    vat_payable = sale_vat - purchase_vat

    # --- Prepare CSV output ---
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Type", "Amount (₱)"])
    writer.writerow(["Input VAT (from Purchases)", f"{purchase_vat:.2f}"])
    writer.writerow(["Output VAT (from Sales)", f"{sale_vat:.2f}"])

    if vat_payable >= 0:
        writer.writerow(["VAT Payable", f"{vat_payable:.2f}"])
    else:
        writer.writerow(["VAT Refund", f"{abs(vat_payable):.2f}"])

    output.seek(0)

    # --- Return downloadable CSV ---
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=vat_report.csv"
        },
    )





@core_bp.route('/api/product/<sku>')
def api_product(sku):
    product = Product.query.filter_by(sku=sku).first()
    if not product:
        return jsonify({'error': 'Product not found'}), 404

    return jsonify({
        'sku': product.sku,
        'name': product.name,
        'sale_price': float(product.sale_price or 0),
        'cost_price': float(product.cost_price or 0),
        'quantity': product.quantity
    })



# --- ADD THIS LOGIN ROUTE ---
@core_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("15 per minute") 
def login():
    """Handles user login."""
    if current_user.is_authenticated:
        return redirect(url_for('core.index'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # --- ADD THIS: Basic Input Validation ---
        if not username or not password:
            flash('Username and password are required.', 'danger')
            return render_template('login.html'), 400
        
        if len(username) > 100 or len(password) > 100:
            flash('Username or password is too long.', 'danger')
            return render_template('login.html'), 400

        user = User.query.filter_by(username=username).first()

        if user and pbkdf2_sha256.verify(password, user.password_hash):
            login_user(user)
            log_action(f'User logged in successfully.', user=user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('core.index'))
        else:
            log_action(f'Failed login attempt for username: {username}.')
            flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@core_bp.route('/reset-password', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def reset_password_form():
    if request.method == 'POST':
        username = request.form.get('username')
        new_password = request.form.get('password')

        if not username or not new_password:
            flash('Username and new password are required.', 'danger')
            return redirect(url_for('core.reset_password_form'))

        user = User.query.filter_by(username=username).first()
        if user:
            # Hash the new password and update the user
            user.password_hash = pbkdf2_sha256.hash(new_password)
            db.session.commit()
            
            # Log this action (without a specific user in session)
            log_action(f'Password for user {username} was reset via TIN verification.')

            flash('Password has been reset successfully. You can now log in.', 'success')
            return redirect(url_for('core.login'))
        else:
            flash('User not found.', 'danger')

    # For the GET request, fetch all users to populate the dropdown
    all_users = User.query.order_by(User.username).all()
    return render_template('reset_password.html', users=all_users)


@core_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def forgot_password():
    if request.method == 'POST':
        tin = request.form.get('tin')
        company = CompanyProfile.query.first()

        if company and company.tin == tin:
            return redirect(url_for('core.reset_password_form'))
        else:
            flash('The provided TIN does not match our records.', 'danger')

    return render_template('forgot_password.html')


@core_bp.route('/logout')
@login_required
def logout():
    """Handles user logout."""
    # 1. Capture the user's ID and username before logging out
    user_id_to_log = current_user.id
    username_to_log = current_user.username

    # 2. Log the user out, which clears the session
    logout_user()

    # 3. Manually create the AuditLog entry with the saved details
    try:
        log = AuditLog(
            user_id=user_id_to_log,
            action=f'User {username_to_log} logged out successfully.',
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        # In case of a database error, we don't want to crash the logout process
        # You could add proper logging here if needed
        print(f"Error creating audit log for logout: {e}")
        db.session.rollback()

    flash('You have been logged out.', 'info')
    return redirect(url_for('core.login'))


@core_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@role_required('Admin')
def settings():
    # We assume only one company profile exists
    profile = CompanyProfile.query.first_or_404()
    if request.method == 'POST':
        profile.name = request.form.get('name')
        profile.tin = request.form.get('tin')
        profile.address = request.form.get('address')
        profile.business_style = request.form.get('business_style')
        log_action(f'Updated Company Profile settings.')
        db.session.commit()
        flash('Company profile updated successfully!', 'success')
        return redirect(url_for('core.settings'))
    
    all_users = User.query.order_by(User.username).all()
    return render_template('settings.html', profile=profile, users=all_users)

@core_bp.route('/inventory/adjust', methods=['POST'])
@login_required
@role_required('Admin', 'Accountant')
def adjust_stock():
    product_id = int(request.form.get('product_id'))
    quantity = int(request.form.get('quantity'))
    reason = request.form.get('reason')
    
    product = Product.query.get_or_404(product_id)

    if not reason:
        flash('A reason for the adjustment is required.', 'danger')
        return redirect(url_for('core.inventory'))

    try:
        # 1. Update Product Quantity
        original_qty = product.quantity
        product.quantity += quantity
        
        # 2. Create Stock Adjustment Log
        adjustment = StockAdjustment(
            product_id=product.id,
            quantity_changed=quantity,
            reason=reason,
            user_id=current_user.id
        )
        db.session.add(adjustment)

        # 3. Create Journal Entry
        adjustment_value = abs(quantity) * product.cost_price
        
        if quantity < 0: # Stock reduction (loss)
            debit_account_code = "505"  # 505: Inventory Loss
            credit_account_code = "120" # 120: Inventory
            desc = f"Stock loss for {product.name}: {reason}"
        else: # Stock increase (gain)
            debit_account_code = "120"  # 120: Inventory
            credit_account_code = "406" # 406: Inventory Gain
            desc = f"Stock gain for {product.name}: {reason}"

        je_lines = [
            {"account_code": debit_account_code, "debit": adjustment_value, "credit": 0},
            {"account_code": credit_account_code, "debit": 0, "credit": adjustment_value}
        ]
        journal = JournalEntry(description=desc, entries_json=json.dumps(je_lines))
        db.session.add(journal)

        log_action(f'Adjusted stock for {product.name} by {quantity}. Reason: {reason}.')
        db.session.commit()
        flash(f'Stock for {product.name} adjusted successfully.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error adjusting stock: {str(e)}', 'danger')
        
    return redirect(url_for('core.inventory'))


# Add this new route for viewing the logs
@core_bp.route('/audit-log')
@login_required
@role_required('Admin')
def audit_log():
    """Display the audit log."""
    page = request.args.get('page', 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=25)
    return render_template('audit_log.html', logs=logs)