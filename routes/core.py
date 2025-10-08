from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response
from models import db, Product, Purchase, PurchaseItem, Sale, SaleItem, JournalEntry
import json
from config import Config
from datetime import datetime, timedelta
from sqlalchemy import func
from models import Sale
import io, csv, json
from io import StringIO
from routes.utils import paginate_query


core_bp = Blueprint('core', __name__)
VAT_RATE = Config.VAT_RATE



@core_bp.route('/')
def index():
    # --- Base data ---
    products = Product.query.all()
    low_stock = [p for p in products if p.quantity <= 5]

    # --- Summary data ---
    total_sales = db.session.query(func.sum(Sale.total)).scalar() or 0
    total_purchases = db.session.query(func.sum(Purchase.total)).scalar() or 0
    total_inventory_value = sum(p.cost_price * p.quantity for p in products)

    # --- Income summary ---
    net_income = total_sales - total_purchases

    # --- Chart data (sales trend over 7 days) ---
    today = datetime.utcnow().date()
    last_7_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

    sales_by_day = []
    for day in last_7_days:
        day_total = (
            db.session.query(func.sum(Sale.total))
            .filter(func.date(Sale.created_at) == day)
            .scalar()
            or 0
        )
        sales_by_day.append(day_total)

    labels = [d.strftime('%b %d') for d in last_7_days]

    return render_template(
        'index.html',
        products=products,
        low_stock=low_stock,
        total_sales=total_sales,
        total_purchases=total_purchases,
        total_inventory_value=total_inventory_value,
        net_income=net_income,
        labels=labels,
        sales_by_day=sales_by_day,
    )


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



@core_bp.route('/purchase', methods=['GET', 'POST'])
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
    sale = Sale(total=0, vat=0)
    db.session.add(sale)
    db.session.flush()

    total = vat_total = cogs_total = 0
    for it in items:
        sku = it['sku']
        qty = int(it['qty'])
        product = Product.query.filter_by(sku=sku).first()
        if not product:
            return jsonify({'error': f'Product {sku} not found'}), 404
        if product.quantity < qty:
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
    db.session.add(JournalEntry(description=f'Sale #{sale.id}', entries_json=json.dumps(je_lines)))
    db.session.commit()
    return jsonify({'status': 'ok', 'sale_id': sale.id})


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