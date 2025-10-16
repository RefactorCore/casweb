from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response, session
from models import db, User, Product, Purchase, PurchaseItem, Sale, SaleItem, JournalEntry
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
    low_stock = [p for p in products if p.quantity <= 5]

    # --- Summary data (ALL TIME) ---
    total_sales = db.session.query(func.sum(Sale.total)).scalar() or 0
    total_purchases = db.session.query(func.sum(Purchase.total)).scalar() or 0
    total_inventory_value = sum(p.cost_price * p.quantity for p in products)

    # --- Income summary (ALL TIME) ---
    net_income = total_sales - total_purchases
    
    # --- Last 30 Days and Last 12 Hours Summary Data ---
    today = datetime.utcnow()
    last_30_days_ago = today - timedelta(days=30)
    last_12_hours_ago = today - timedelta(hours=12)

    sales_30d = db.session.query(func.sum(Sale.total)).filter(
        Sale.created_at >= last_30_days_ago
    ).scalar() or 0
    purchases_30d = db.session.query(func.sum(Purchase.total)).filter(
        Purchase.created_at >= last_30_days_ago
    ).scalar() or 0
    net_income_30d = sales_30d - purchases_30d

    sales_12h = db.session.query(func.sum(Sale.total)).filter(
        Sale.created_at >= last_12_hours_ago
    ).scalar() or 0
    purchases_12h = db.session.query(func.sum(Purchase.total)).filter(
        Purchase.created_at >= last_12_hours_ago
    ).scalar() or 0
    net_income_12h = sales_12h - purchases_12h

    # 📈 --- MODIFIED: Chart data (sales trend by 12H, 7D, or 30D) ---
    # Get 'period' parameter from URL, default to 7 if not present or invalid
    period = request.args.get('period', '7')
    
    sales_by_period = []
    labels = []
    current_filter_label = ''

    if period == '12':
        current_filter_label = 'Last 12 Hours'
        # Generate 12 hourly intervals ending with the current hour
        intervals = [today - timedelta(hours=i) for i in range(11, -1, -1)]
        
        for hour_start in intervals:
            hour_end = hour_start + timedelta(hours=1)
            hour_total = (
                db.session.query(func.sum(Sale.total))
                .filter(Sale.created_at >= hour_start)
                .filter(Sale.created_at < hour_end)
                .scalar()
                or 0
            )
            sales_by_period.append(hour_total)
            labels.append(hour_start.strftime('%I%p')) # e.g., 09AM, 10PM

    else: # Default to 7 days, or use 30 days
        if period == '30':
            days = 30
            current_filter_label = 'Last 30 Days'
        else:
            days = 7
            current_filter_label = 'Last 7 Days'
            
        today_date = datetime.utcnow().date()
        last_n_days = [today_date - timedelta(days=i) for i in range(days - 1, -1, -1)]

        for day in last_n_days:
            day_total = (
                db.session.query(func.sum(Sale.total))
                .filter(func.date(Sale.created_at) == day)
                .scalar()
                or 0
            )
            sales_by_period.append(day_total)
            labels.append(day.strftime('%b %d'))

    # -------------------

    # --- Top Selling Products (by Quantity Sold) ---
    top_sellers = (
        db.session.query(
            Product.name,
            func.sum(SaleItem.qty).label('total_qty_sold')
        )
        .join(SaleItem, Product.id == SaleItem.product_id)
        .group_by(Product.name)
        .order_by(func.sum(SaleItem.qty).desc())
        .limit(5)
        .all()
    )

    return render_template(
        'index.html',
        products=products,
        low_stock=low_stock,
        total_sales=total_sales,
        total_purchases=total_purchases,
        total_inventory_value=total_inventory_value,
        net_income=net_income,
        labels=labels,
        sales_by_day=sales_by_period, # Pass the dynamic data here
        top_sellers=top_sellers,
        sales_30d=sales_30d,
        net_income_30d=net_income_30d,
        sales_24h=sales_12h, 
        net_income_24h=net_income_12h,
        current_period_filter=period, # NEW: Pass the current filter value
        current_filter_label=current_filter_label # NEW: Pass the label for the title
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
    query = query.order_by(Product.name.asc())
    pagination = paginate_query(query, per_page=12) # Using 2 for testing

    # 👇 NEW: Create a dictionary of current arguments, excluding 'page'
    # This is the fix for the pagination link error.
    safe_args = {k: v for k, v in request.args.items() if k != 'page'}
    
    return render_template(
        'inventory.html',
        products=pagination.items,
        pagination=pagination,
        search=search,
        # 👇 NEW: Pass the filtered arguments dictionary
        safe_args=safe_args
    )

# ✅ Update product
@core_bp.route('/update_product', methods=['POST'])
def update_product():
    sku = request.form.get('sku')
    product = Product.query.filter_by(sku=sku).first()
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('core.inventory'))

    # Update fields
    product.name = request.form.get('name')
    product.sale_price = float(request.form.get('sale_price') or 0)
    product.cost_price = float(request.form.get('cost_price') or 0)
    product.quantity = int(request.form.get('quantity') or 0)

    log_action(f'Updated product SKU: {product.sku}, Name: {product.name}.')
    db.session.commit()
    flash(f'Product {product.sku} updated successfully.', 'success')
    return redirect(url_for('core.inventory'))


# ✅ Delete product
@core_bp.route('/delete_product/<sku>', methods=['POST'])
def delete_product(sku):
    product = Product.query.filter_by(sku=sku).first()
    if not product:
        return jsonify({'error': 'Product not found'}), 404

    db.session.delete(product)
    db.session.commit()
    return jsonify({'status': 'deleted'})


@core_bp.route('/api/add_multiple_products', methods=['POST'])
def api_add_multiple_products():
    from models import Product # Ensure Product is imported
    data = request.json
    products_data = data.get('products', [])
    
    if not products_data:
        return jsonify({'error': 'No product data provided'}), 400

    new_product_count = 0
    
    try:
        for p_data in products_data:
            # Basic validation
            if not p_data.get('sku') or not p_data.get('name'):
                continue
            
            # Check if SKU already exists
            if Product.query.filter_by(sku=p_data.get('sku')).first():
                 # Skip or update, for simplicity we will skip here
                 continue 
                 
            new_prod = Product(
                sku=p_data.get('sku'),
                name=p_data.get('name'),
                sale_price=float(p_data.get('sale_price') or 0),
                cost_price=float(p_data.get('cost_price') or 0),
                quantity=int(p_data.get('quantity') or 0)
            )
            db.session.add(new_prod)
            new_product_count += 1
            
        db.session.commit()
        return jsonify({'status': 'ok', 'count': new_product_count})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@login_required
@core_bp.route('/purchase', methods=['GET', 'POST'])
@role_required('Admin', 'Accountant', 'Cashier')
def purchase():
    if request.method == 'POST':
        try:
            supplier = request.form.get('supplier', '').strip() or 'Unknown'
            items_raw = request.form.get('items_json')
            items = json.loads(items_raw) if items_raw else []

            if not items:
                flash("No items added to the purchase. Please add products first.", "warning")
                return redirect(url_for('core.purchase'))

            # --- Create Purchase record ---
            purchase = Purchase(total=0, vat=0, supplier=supplier)
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
                        quantity=qty
                    )
                    db.session.add(product)
                    db.session.flush()
                else:
                    # Weighted average cost update
                    old_val = product.cost_price * product.quantity
                    new_val = unit_cost * qty
                    product.quantity += qty
                    product.cost_price = (old_val + new_val) / product.quantity

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
                {"account": "Inventory", "debit": total - vat_total, "credit": 0},
                {"account": "VAT Input", "debit": vat_total, "credit": 0},
                {"account": "Accounts Payable", "debit": 0, "credit": total}
            ]
            journal = JournalEntry(
                description=f"Purchase #{purchase.id} - {supplier}",
                entries_json=json.dumps(journal_lines)
            )
            db.session.add(journal)
            db.session.commit()

            flash(f"✅ Purchase #{purchase.id} recorded successfully.", "success")
            return redirect(url_for('core.purchases'))

        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error saving purchase: {str(e)}", "danger")
            return redirect(url_for('core.purchase'))

    # --- GET method ---
    products = Product.query.order_by(Product.name.asc()).all()
    return render_template('purchase.html', products=products)



# ✅ New: List all purchases
@core_bp.route('/purchases')
def purchases():
    purchases = Purchase.query.order_by(Purchase.id.desc()).all()
    return render_template('purchases.html', purchases=purchases)


@core_bp.route('/delete_purchase/<int:purchase_id>', methods=['POST'])
def delete_purchase(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    db.session.delete(purchase)
    db.session.commit()
    return jsonify({'status': 'deleted'})


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
    query = Product.query

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

            line_net = qty * product.sale_price
            vat = round(line_net * VAT_RATE, 2)
            line_total = line_net + vat
            cogs = qty * product.cost_price

            db.session.add(SaleItem(
                sale_id=sale.id, product_id=product.id,
                product_name=product.name, sku=sku,
                qty=qty, unit_price=product.sale_price,
                line_total=line_total, cogs=cogs
            ))

            product.quantity -= qty
            total += line_total
            vat_total += vat
            cogs_total += cogs

        sale.total, sale.vat = total, vat_total

        je_lines = [
            {'account': 'Cash', 'debit': total, 'credit': 0},
            {'account': 'Sales Revenue', 'debit': 0, 'credit': total - vat_total},
            {'account': 'VAT Payable', 'debit': 0, 'credit': vat_total},
            {'account': 'COGS', 'debit': cogs_total, 'credit': 0},
            {'account': 'Inventory', 'debit': 0, 'credit': cogs_total},
        ]
        # --- MODIFIED: Update Journal Entry description ---
        db.session.add(JournalEntry(description=f'Sale #{sale.id} ({full_doc_number})', entries_json=json.dumps(je_lines)))
        
        db.session.commit()
        
        # --- MODIFIED: Return the new document number to the frontend ---
        return jsonify({'status': 'ok', 'sale_id': sale.id, 'receipt_number': full_doc_number})

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

@core_bp.route('/reports')
@role_required('Admin', 'Accountant')
def reports():
    """Display all journal entries with filters and summary."""
    # --- 1. Get Filters ---
    search = request.args.get('search', '').strip()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    query = JournalEntry.query

    # --- 2. Apply Filters ---
    # 🔎 Filter: Search by description or account name in entries_json
    if search:
        query = query.filter(JournalEntry.description.ilike(f"%{search}%") | JournalEntry.entries_json.ilike(f"%{search}%"))

    # 📅 Filter: Date range (Using the robust parse_date helper)
    if start_date:
        start_dt = parse_date(start_date)
        if start_dt:
            query = query.filter(JournalEntry.created_at >= start_dt)
            
    if end_date:
        end_dt = parse_date(end_date)
        if end_dt:
            # Add one day to the end date to include the entire end date
            end_of_day = end_dt + timedelta(days=1)
            query = query.filter(JournalEntry.created_at < end_of_day)


    # --- 3. Compute Summary (Requires all filtered data) ---
    # We query all filtered journals to calculate the full summary before pagination.
    # Note: Using .all() here is only necessary for the summary calculation.
    filtered_journals = query.order_by(JournalEntry.created_at.desc()).all()
    
    total_debit = 0
    total_credit = 0
    for j in filtered_journals:
        for e in j.entries():
            total_debit += float(e.get("debit", 0) or 0)
            total_credit += float(e.get("credit", 0) or 0)

    summary = {
        "count": len(filtered_journals),
        "total_debit": total_debit,
        "total_credit": total_credit,
    }
    
    # --- 4. Paginate Results ---
    # Since query.all() consumes the query, we reset and re-run for pagination
    # A cleaner approach is to use the existing 'query' object before calling .all()
    # (Your original query object is intact, so we can use it here)
    pagination = paginate_query(query.order_by(JournalEntry.created_at.desc()), per_page=10)

    # --- 5. Prepare safe_args for template links ---
    safe_args = {k: v for k, v in request.args.items() if k != 'page'}
    
    return render_template(
        "reports.html", 
        journals=pagination.items, 
        pagination=pagination, 
        summary=summary,
        safe_args=safe_args, # Passed for pagination/export links
        start_date=start_date, # Passed to persist form value
        end_date=end_date      # Passed to persist form value
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



@core_bp.route('/income_statement')
def income_statement():
    journals = JournalEntry.query.all()

    total_revenue = total_cogs = total_expense = 0

    for j in journals:
        for line in j.entries():
            acc = line.get('account', '').lower()
            debit = float(line.get('debit', 0))
            credit = float(line.get('credit', 0))

            if 'sales revenue' in acc:
                total_revenue += credit
            elif 'cogs' in acc:
                total_cogs += debit
            elif acc in ['rent expense', 'utilities expense', 'salaries expense', 'misc expense']:
                total_expense += debit

    gross_profit = total_revenue - total_cogs
    net_income = gross_profit - total_expense

    return render_template(
        'income_statement.html',
        total_revenue=total_revenue,
        total_cogs=total_cogs,
        gross_profit=gross_profit,
        total_expense=total_expense,
        net_income=net_income
    )

@core_bp.route('/export_income_statement', methods=['GET'])
def export_income_statement():
    # Reuse the calculation logic from the income_statement view
    journals = JournalEntry.query.all()

    total_revenue = total_cogs = total_expense = 0

    for j in journals:
        for line in j.entries():
            acc = line.get('account', '').lower()
            debit = float(line.get('debit', 0))
            credit = float(line.get('credit', 0))

            if 'sales revenue' in acc:
                total_revenue += credit
            elif 'cogs' in acc:
                total_cogs += debit
            # Note: This list must match the one used in your /income_statement route
            elif acc in ['rent expense', 'utilities expense', 'salaries expense', 'misc expense']:
                total_expense += debit

    gross_profit = total_revenue - total_cogs
    net_income = gross_profit - total_expense

    # --- Prepare CSV output ---
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(["Description", "Amount (₱)"])
    writer.writerow(["Sales Revenue", f"{total_revenue:.2f}"])
    writer.writerow(["Cost of Goods Sold (COGS)", f"{-total_cogs:.2f}"])
    writer.writerow(["Gross Profit", f"{gross_profit:.2f}"])
    writer.writerow(["Operating Expenses", f"{-total_expense:.2f}"])
    writer.writerow(["Net Income", f"{net_income:.2f}"])

    output.seek(0)
    filename = f"income_statement_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
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

def aggregate_journal_entries(journals):
    """Aggregates all debit/credit movements by account."""
    ledger = {}
    for j in journals:
        # Use the entries() method from the JournalEntry model
        for line in j.entries():
            account = line.get('account')
            debit = float(line.get('debit', 0) or 0)
            credit = float(line.get('credit', 0) or 0)
            
            if account not in ledger:
                ledger[account] = {'debit': 0.0, 'credit': 0.0}
            
            ledger[account]['debit'] += debit
            ledger[account]['credit'] += credit
            
    return ledger


@core_bp.route('/general_ledger')
def general_ledger():
    # Fetch all journal entries (you might want to add date filters later)
    journals = JournalEntry.query.order_by(JournalEntry.created_at.asc()).all()
    
    # Aggregate the data
    gl_summary = aggregate_journal_entries(journals)
    
    # Calculate balances for presentation
    gl_with_balances = []
    for account, totals in gl_summary.items():
        balance = totals['debit'] - totals['credit']
        gl_with_balances.append({
            'account': account,
            'debit': totals['debit'],
            'credit': totals['credit'],
            'balance': balance,
            # Determine if it's a Debit (Asset/Expense) or Credit (Lia/Equity/Revenue) balance
            'balance_type': 'Debit' if balance >= 0 else 'Credit' 
        })
        
    # Sort accounts alphabetically for easier reading
    gl_with_balances.sort(key=lambda x: x['account'])
    
    return render_template('general_ledger.html', gl_data=gl_with_balances)

@core_bp.route('/export_general_ledger', methods=['GET'])
def export_general_ledger():
    # Reuse the logic from the general_ledger view
    journals = JournalEntry.query.order_by(JournalEntry.created_at.asc()).all()
    gl_summary = aggregate_journal_entries(journals)
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header row
    writer.writerow(["Account", "Total Debits", "Total Credits", "Balance", "Balance Type"])

    # Write data rows
    for account, totals in gl_summary.items():
        balance = totals['debit'] - totals['credit']
        balance_type = 'Debit' if balance >= 0 else 'Credit'
        
        writer.writerow([
            account,
            f"{totals['debit']:.2f}",
            f"{totals['credit']:.2f}",
            f"{balance:.2f}",
            balance_type
        ])

    output.seek(0)
    filename = f"general_ledger_summary_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@core_bp.route('/balance_sheet')
def balance_sheet():
    # 1. Get the aggregated ledger data (reusing the logic from GL)
    journals = JournalEntry.query.all()
    gl_summary = aggregate_journal_entries(journals)
    
    # 2. Define account classifications (adjust these based on your exact chart of accounts)
    account_classes = {
        'Asset': ['Cash', 'Inventory', 'Accounts Receivable'],
        'Liability': ['Accounts Payable', 'VAT Payable'],
        # Net Income from Income Statement will be added to Equity
        'Equity': ['Owner\'s Equity']
    }
    
    # 3. Calculate Income for Retained Earnings (Net Income affects Equity)
    # This relies on your existing income_statement logic
    journals = JournalEntry.query.all()
    total_revenue = total_cogs = total_expense = 0
    for j in journals:
        for line in j.entries():
            acc = line.get('account', '').lower()
            debit = float(line.get('debit', 0))
            credit = float(line.get('credit', 0))
            if 'sales revenue' in acc:
                total_revenue += credit
            elif 'cogs' in acc:
                total_cogs += debit
            elif 'expense' in acc: # Simple check for all expenses
                total_expense += debit

    net_income = total_revenue - total_cogs - total_expense
    
    # 4. Group balances
    assets = []
    liabilities = []
    equity = []
    
    # Initialize totals
    total_assets = 0.0
    total_liabilities = 0.0
    
    # Process accounts from the ledger
    for account, totals in gl_summary.items():
        balance = totals['debit'] - totals['credit']
        
        # Check against pre-defined asset accounts
        if account in account_classes['Asset']:
            assets.append({'account': account, 'balance': balance})
            total_assets += balance
        
        # Check against pre-defined liability accounts
        elif account in account_classes['Liability']:
            liabilities.append({'account': account, 'balance': -balance}) # Liabilities have normal credit balances
            total_liabilities += -balance
            
        # Check against pre-defined equity accounts
        elif account in account_classes['Equity']:
             # Use the balance (normal credit) for existing equity accounts
            equity.append({'account': account, 'balance': -balance})
            
    # Add Net Income (Retained Earnings) to Equity
    equity.append({'account': 'Current Period Net Income', 'balance': net_income})
    total_equity = sum(e['balance'] for e in equity)
    
    # Total Liabilites and Equity
    total_liabilities_and_equity = total_liabilities + total_equity
    
    # Note: For a fully compliant system, you'd need a more robust Chart of Accounts model 
    # to automatically determine an account's type (Asset, Liability, etc.)
    
    return render_template(
        'balance_sheet.html',
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        total_liabilities_and_equity=total_liabilities_and_equity
    )

@core_bp.route('/export_balance_sheet', methods=['GET'])
def export_balance_sheet():
    # Reuse the calculation logic from the balance_sheet view
    journals = JournalEntry.query.all()
    gl_summary = aggregate_journal_entries(journals)
    
    # 2. Define account classifications (must match the view logic)
    account_classes = {
        'Asset': ['Cash', 'Inventory', 'Accounts Receivable'],
        'Liability': ['Accounts Payable', 'VAT Payable'],
        'Equity': ['Owner\'s Equity']
    }
    
    # 3. Calculate Net Income (must match the view logic)
    # Note: For production, this should be a dedicated utility function
    total_revenue = total_cogs = total_expense = 0
    for j in journals:
        for line in j.entries():
            acc = line.get('account', '').lower()
            debit = float(line.get('debit', 0))
            credit = float(line.get('credit', 0))
            if 'sales revenue' in acc:
                total_revenue += credit
            elif 'cogs' in acc:
                total_cogs += debit
            elif 'expense' in acc: 
                total_expense += debit

    net_income = total_revenue - total_cogs - total_expense
    
    # 4. Group balances and calculate totals
    assets_data = []
    liabilities_data = []
    equity_data = []
    total_assets = 0.0
    total_liabilities = 0.0
    
    for account, totals in gl_summary.items():
        balance = totals['debit'] - totals['credit']
        
        if account in account_classes['Asset']:
            assets_data.append({'account': account, 'balance': balance})
            total_assets += balance
        elif account in account_classes['Liability']:
            liabilities_data.append({'account': account, 'balance': -balance})
            total_liabilities += -balance
        elif account in account_classes['Equity']:
            equity_data.append({'account': account, 'balance': -balance})
            
    equity_data.append({'account': 'Current Period Net Income', 'balance': net_income})
    total_equity = sum(e['balance'] for e in equity_data)
    total_liabilities_and_equity = total_liabilities + total_equity
    
    # --- Prepare CSV output ---
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write Assets Section
    writer.writerow(["ASSETS", ""])
    for item in assets_data:
        writer.writerow([item['account'], f"{item['balance']:.2f}"])
    writer.writerow(["TOTAL ASSETS", f"{total_assets:.2f}"])
    writer.writerow([]) # Blank line for separation
    
    # Write Liabilities Section
    writer.writerow(["LIABILITIES", ""])
    for item in liabilities_data:
        writer.writerow([item['account'], f"{item['balance']:.2f}"])
    writer.writerow(["TOTAL LIABILITIES", f"{total_liabilities:.2f}"])
    writer.writerow([])
    
    # Write Equity Section
    writer.writerow(["EQUITY", ""])
    for item in equity_data:
        writer.writerow([item['account'], f"{item['balance']:.2f}"])
    writer.writerow(["TOTAL EQUITY", f"{total_equity:.2f}"])
    writer.writerow([])
    
    # Write Summary Row
    writer.writerow(["TOTAL LIABILITIES & EQUITY", f"{total_liabilities_and_equity:.2f}"])


    output.seek(0)
    filename = f"balance_sheet_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


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
            debit_account = "Inventory Loss" # Make sure this account exists
            credit_account = "Inventory"
            desc = f"Stock loss for {product.name}: {reason}"
        else: # Stock increase (gain)
            debit_account = "Inventory"
            credit_account = "Inventory Gain" # Make sure this account exists
            desc = f"Stock gain for {product.name}: {reason}"

        je_lines = [
            {"account": debit_account, "debit": adjustment_value, "credit": 0},
            {"account": credit_account, "debit": 0, "credit": adjustment_value}
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