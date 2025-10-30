from flask import Blueprint, render_template, request, abort, Response
from flask_login import login_required
# Add CompanyProfile, Customer, Supplier, CreditMemo
from models import db, JournalEntry, Account, Sale, Purchase, Product, ARInvoice, APInvoice, CompanyProfile, Customer, Supplier, CreditMemo, Payment, SaleItem, PurchaseItem, StockAdjustment
from collections import defaultdict
import json
from sqlalchemy import func, extract, cast, Date
from datetime import datetime, date, timedelta
from routes.decorators import role_required
import io
import csv

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')


def parse_date(date_str):
    """Helper to safely parse YYYY-MM-DD format strings."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None

@reports_bp.route('/trial-balance')
@login_required
@role_required('Admin', 'Accountant')
def trial_balance():
    # --- MODIFIED: Get dates from URL ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    
    # --- MODIFIED: Pass dates to the aggregator ---
    agg = aggregate_account_balances(start_date, end_date)
    
    tb = []
    total_debit = 0.0
    total_credit = 0.0
    
    for acc_code, val in agg.items():
        acc_details = Account.query.filter_by(code=acc_code).first()
        acc_name = acc_details.name if acc_details else f"Unknown ({acc_code})"
        
        if val >= 0:
            tb.append({'code': acc_code, 'name': acc_name, 'debit': val, 'credit': 0.0})
            total_debit += val
        else:
            tb.append({'code': acc_code, 'name': acc_name, 'debit': 0.0, 'credit': -val})
            total_credit += -val
            
    tb.sort(key=lambda x: x['code'])
    
    # --- MODIFIED: Pass dates back to the template ---
    return render_template('trial_balance.html', tb=tb, 
                           total_debit=total_debit, total_credit=total_credit,
                           start_date=start_date_str, end_date=end_date_str)


@reports_bp.route('/ledger/<code>')
@login_required
@role_required('Admin', 'Accountant')
def ledger(code):
    account = Account.query.filter_by(code=code).first_or_404()
    
    # --- MODIFIED: Get dates from URL ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    
    # --- MODIFIED: Filter the Journal Entry query by date ---
    query = JournalEntry.query.order_by(JournalEntry.created_at)
    if start_date:
        query = query.filter(JournalEntry.created_at >= start_date)
    if end_date:
        end_date_inclusive = end_date + timedelta(days=1)
        query = query.filter(JournalEntry.created_at < end_date_inclusive)
    
    rows = []
    balance = 0.0
    
    # --- MODIFIED: Get running balance *before* the start date (if one exists) ---
    if start_date:
        opening_balance_query = JournalEntry.query.filter(JournalEntry.created_at < start_date)
        for je in opening_balance_query.all():
            for line in je.entries():
                if line.get('account_code') == code:
                    debit = float(line.get('debit', 0) or 0)
                    credit = float(line.get('credit', 0) or 0)
                    balance += debit - credit
        
        # Add the opening balance as the first row
        rows.append({'date': start_date, 'desc': 'Opening Balance', 'debit': 0, 'credit': 0, 'balance': balance})


    # --- MODIFIED: Loop through the *filtered* query ---
    for je in query.all():
        for line in je.entries():
            if line.get('account_code') == code:
                debit = float(line.get('debit', 0) or 0)
                credit = float(line.get('credit', 0) or 0)
                balance += debit - credit
                rows.append({'date': je.created_at, 'desc': je.description, 'debit': debit, 'credit': credit, 'balance': balance})
    
    # --- MODIFIED: Pass dates back to the template ---
    return render_template('ledger.html', account=account, rows=rows, 
                           balance=balance, start_date=start_date_str, end_date=end_date_str)


@reports_bp.route('/balance-sheet')
@login_required
@role_required('Admin', 'Accountant')
def balance_sheet():
    # --- MODIFIED: Balance Sheet is "As of" a date (end_date) ---
    # Default to today if no date is provided
    default_end_date = datetime.utcnow().strftime('%Y-%m-%d')
    end_date_str = request.args.get('end_date', default_end_date)
    end_date = parse_date(end_date_str)
    
    # --- MODIFIED: Pass only the end_date to the aggregator ---
    # This gets all transactions from the beginning of time *up to* this date
    agg = aggregate_account_balances(start_date=None, end_date=end_date)
    
    assets, liabilities, equity = [], [], []
    
    for acc_code, bal in agg.items():
        acct_rec = Account.query.filter_by(code=acc_code).first()
        if not acct_rec:
            continue 
            
        acc_name = acct_rec.name
        acc_type = acct_rec.type

        if acc_type == 'Asset':
            assets.append((acc_name, bal))
        elif acc_type == 'Liability':
            liabilities.append((acc_name, -bal))
        elif acc_type == 'Equity':
            equity.append((acc_name, -bal))

    # --- MODIFIED: Calculate Net Income *up to the end_date* ---
    net_income = 0.0
    # We re-call the aggregator just for Revenue/Expense accounts
    is_agg = aggregate_account_balances(start_date=None, end_date=end_date)
    revenues = {code: -bal for code, bal in is_agg.items() if Account.query.filter_by(code=code, type='Revenue').first()}
    expenses = {code: bal for code, bal in is_agg.items() if Account.query.filter_by(code=code, type='Expense').first()}
    
    total_revenue = sum(revenues.values())
    total_expense = sum(expenses.values())
    net_income = total_revenue - total_expense
    
    equity.append(("Current Period Net Income", net_income))

    total_assets = sum(b for a, b in assets)
    total_liabilities = sum(b for a, b in liabilities)
    total_equity = sum(b for a, b in equity)
    
    # --- MODIFIED: Pass the end_date back to the template ---
    return render_template('balance_sheet.html', assets=assets, liabilities=liabilities, equity=equity,
                           total_assets=total_assets, total_liabilities=total_liabilities, total_equity=total_equity,
                           end_date=end_date_str)


@reports_bp.route('/income-statement')
@login_required
@role_required('Admin', 'Accountant')
def income_statement():
    # --- MODIFIED: Get dates from URL ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    # --- MODIFIED: Pass dates to the aggregator ---
    agg = aggregate_account_balances(start_date, end_date)
    
    revenues, expenses = {}, {}

    for acc_code, bal in agg.items():
        acct_rec = Account.query.filter_by(code=acc_code).first()
        if not acct_rec:
            continue

        acc_name = acct_rec.name
        acc_type = acct_rec.type

        if acc_type == 'Revenue':
            revenues[acc_name] = -bal
        elif acc_type == 'Expense':
            expenses[acc_name] = bal

    total_revenue = sum(revenues.values())
    total_expense = sum(expenses.values())
    net_income = total_revenue - total_expense
    
    # --- MODIFIED: Pass dates back to the template ---
    return render_template('income_statement.html', revenues=revenues, expenses=expenses,
                           total_revenue=total_revenue, total_expense=total_expense, net_income=net_income,
                           start_date=start_date_str, end_date=end_date_str)


@reports_bp.route('/vat-report')
@login_required
@role_required('Admin', 'Accountant')
def vat_report():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    # Prepare base queries for each source
    sale_query = Sale.query
    ar_invoice_query = ARInvoice.query
    purchase_query = Purchase.query
    ap_invoice_query = APInvoice.query

    # Make end_date inclusive for datetime comparisons
    end_date_inclusive = None
    if end_date:
        end_date_inclusive = end_date + timedelta(days=1)

    # Apply date filters where appropriate
    if start_date:
        sale_query = sale_query.filter(Sale.created_at >= start_date)
        ar_invoice_query = ar_invoice_query.filter(ARInvoice.date >= start_date)
        purchase_query = purchase_query.filter(Purchase.created_at >= start_date)
        ap_invoice_query = ap_invoice_query.filter(APInvoice.date >= start_date)

    if end_date_inclusive:
        sale_query = sale_query.filter(Sale.created_at < end_date_inclusive)
        ar_invoice_query = ar_invoice_query.filter(ARInvoice.date < end_date_inclusive)
        purchase_query = purchase_query.filter(Purchase.created_at < end_date_inclusive)
        ap_invoice_query = ap_invoice_query.filter(APInvoice.date < end_date_inclusive)

    # --- NON-VAT AWARE SUMS (use DB aggregates for performance) ---
    # Vatable cash sales VAT
    sales_vat = float(
        sale_query.filter(Sale.is_vatable == True)
        .with_entities(func.coalesce(func.sum(Sale.vat), 0))
        .scalar() or 0
    )
    # Vatable AR invoices VAT
    ar_invoice_vat = float(
        ar_invoice_query.filter(ARInvoice.vat != None, ARInvoice.vat > 0)
        .with_entities(func.coalesce(func.sum(ARInvoice.vat), 0))
        .scalar() or 0
    )
    total_output_vat = sales_vat + ar_invoice_vat

    # Non-VAT Sales totals (cash + AR)
    nonvat_sales_total = float(
        sale_query.filter((Sale.is_vatable == False) | (Sale.is_vatable == None))
        .with_entities(func.coalesce(func.sum(Sale.total), 0))
        .scalar() or 0
    )
    nonvat_ar_total = float(
        ar_invoice_query.filter((ARInvoice.vat == 0) | (ARInvoice.vat == None))
        .with_entities(func.coalesce(func.sum(ARInvoice.total), 0))
        .scalar() or 0
    )
    total_nonvat_sales = nonvat_sales_total + nonvat_ar_total

    # Vatable purchases VAT
    purchases_vat = float(
        purchase_query.filter(Purchase.is_vatable == True)
        .with_entities(func.coalesce(func.sum(Purchase.vat), 0))
        .scalar() or 0
    )
    ap_invoice_vat = float(
        ap_invoice_query.filter(APInvoice.vat != None, APInvoice.vat > 0)
        .with_entities(func.coalesce(func.sum(APInvoice.vat), 0))
        .scalar() or 0
    )
    total_input_vat = purchases_vat + ap_invoice_vat

    # Non-VAT Purchases totals (cash + AP)
    nonvat_purchases_total = float(
        purchase_query.filter((Purchase.is_vatable == False) | (Purchase.is_vatable == None))
        .with_entities(func.coalesce(func.sum(Purchase.total), 0))
        .scalar() or 0
    )
    nonvat_ap_total = float(
        ap_invoice_query.filter((APInvoice.vat == 0) | (APInvoice.vat == None))
        .with_entities(func.coalesce(func.sum(APInvoice.total), 0))
        .scalar() or 0
    )
    total_nonvat_purchases = nonvat_purchases_total + nonvat_ap_total

    vat_payable = total_output_vat - total_input_vat

    return render_template(
        'vat_report.html',
        total_output_vat=total_output_vat,
        total_input_vat=total_input_vat,
        vat_payable=vat_payable,
        total_nonvat_sales=total_nonvat_sales,
        total_nonvat_purchases=total_nonvat_purchases,
        start_date=start_date_str,
        end_date=end_date_str
    )


@reports_bp.route('/sales')
@login_required
def sales():
    sales = Sale.query.order_by(Sale.created_at.desc()).all()
    return render_template('sales.html', sales=sales)


@reports_bp.route('/purchases')
@role_required('Admin', 'Accountant')
@login_required
def purchases():
    purchases = Purchase.query.order_by(Purchase.created_at.desc()).all()
    return render_template('purchases.html', purchases=purchases)

@reports_bp.route('/vat-return')
@login_required
@role_required('Admin', 'Accountant')
def vat_return():
    """Generates data for BIR Form 2550M/Q."""
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    year, month_num = map(int, month.split('-'))

    # Output Tax (from Sales)
    sales_in_month = ARInvoice.query.filter(
        extract('year', ARInvoice.date) == year,
        extract('month', ARInvoice.date) == month_num
    ).all()

    # --- ADD THIS QUERY ---
    cash_sales_in_month = Sale.query.filter(
        extract('year', Sale.created_at) == year,
        extract('month', Sale.created_at) == month_num,
        Sale.is_vatable == True
    ).all()
    # --- END ADD ---
    
    # Adjustments to Output Tax (from Credit Memos)
    returns_in_month = CreditMemo.query.filter(
        extract('year', CreditMemo.date) == year,
        extract('month', CreditMemo.date) == month_num
    ).all()

    # --- UPDATE THESE TWO LINES ---
    total_sales_net = sum(s.total - s.vat for s in sales_in_month) + \
                      sum(s.total - s.vat for s in cash_sales_in_month)
    total_output_vat = sum(s.vat for s in sales_in_month) + \
                       sum(s.vat for s in cash_sales_in_month)
    # --- END UPDATE ---
    
    total_returns_net = sum(cm.amount_net for cm in returns_in_month)
    total_returns_vat = sum(cm.vat for cm in returns_in_month)

    # Input Tax (from Purchases)
    purchases_in_month = APInvoice.query.filter(
        extract('year', APInvoice.date) == year,
        extract('month', APInvoice.date) == month_num
    ).all()

    # --- ADD THIS QUERY ---
    cash_purchases_in_month = Purchase.query.filter(
        extract('year', Purchase.created_at) == year,
        extract('month', Purchase.created_at) == month_num,
        Purchase.is_vatable == True
    ).all()
    # --- END ADD ---
    
    # --- UPDATE THESE TWO LINES ---
    total_purchases_net = sum(p.total - p.vat for p in purchases_in_month) + \
                          sum(p.total - p.vat for p in cash_purchases_in_month)
    total_input_vat = sum(p.vat for p in purchases_in_month) + \
                      sum(p.vat for p in cash_purchases_in_month)
    # --- END UPDATE ---
    
    # Calculation
    net_sales = total_sales_net - total_returns_net
    net_output_vat = total_output_vat - total_returns_vat
    vat_payable = net_output_vat - total_input_vat

    return render_template('vat_return.html', month=month,
                           net_sales=net_sales, net_output_vat=net_output_vat,
                           total_purchases_net=total_purchases_net, total_input_vat=total_input_vat,
                           vat_payable=vat_payable)


@reports_bp.route('/summary-list-sales')
@login_required
@role_required('Admin', 'Accountant')
def summary_list_sales():
    """Generates Summary List of Sales (SLS)."""
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    year, month_num = map(int, month.split('-'))

    sales = db.session.query(
        Customer.tin,
        Customer.name,
        func.sum(ARInvoice.total - ARInvoice.vat).label('net_sales'),
        func.sum(ARInvoice.vat).label('output_vat')
    ).join(Customer, ARInvoice.customer_id == Customer.id).filter(
        extract('year', ARInvoice.date) == year,
        extract('month', ARInvoice.date) == month_num
    ).group_by(Customer.tin, Customer.name).order_by(Customer.name).all()

    grand_total_net = sum(s.net_sales for s in sales)
    grand_total_vat = sum(s.output_vat for s in sales)

    return render_template('sls.html', month=month, sales=sales,
                           grand_total_net=grand_total_net, grand_total_vat=grand_total_vat)


@reports_bp.route('/summary-list-purchases')
@login_required
@role_required('Admin', 'Accountant')
def summary_list_purchases():
    """Generates Summary List of Purchases (SLP)."""
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    year, month_num = map(int, month.split('-'))

    purchases = db.session.query(
        Supplier.tin,
        Supplier.name,
        func.sum(APInvoice.total - APInvoice.vat).label('net_purchases'),
        func.sum(APInvoice.vat).label('input_vat')
    ).join(Supplier, APInvoice.supplier_id == Supplier.id).filter(
        extract('year', APInvoice.date) == year,
        extract('month', APInvoice.date) == month_num
    ).group_by(Supplier.tin, Supplier.name).order_by(Supplier.name).all()

    grand_total_net = sum(p.net_purchases for p in purchases)
    grand_total_vat = sum(p.input_vat for p in purchases)

    return render_template('slp.html', month=month, purchases=purchases,
                           grand_total_net=grand_total_net, grand_total_vat=grand_total_vat)

@reports_bp.route('/form-2307-report')
@login_required
@role_required('Admin', 'Accountant')
def form_2307_report():
    """Generates data for BIR Form 2307 from payments received."""
    customers = Customer.query.order_by(Customer.name).all()
    selected_customer_id = request.args.get('customer_id', type=int)
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    year, month_num = map(int, month.split('-'))
    
    payments = []
    customer = None
    if selected_customer_id:
        customer = Customer.query.get(selected_customer_id)
        payments_query = Payment.query.join(ARInvoice, Payment.ref_id == ARInvoice.id).filter(
            Payment.ref_type == 'AR',
            Payment.wht_amount > 0,
            ARInvoice.customer_id == selected_customer_id,
            extract('year', Payment.date) == year,
            extract('month', Payment.date) == month_num
        )
        payments = payments_query.all()

    company = CompanyProfile.query.first()
    
    return render_template('form_2307_report.html', customers=customers, 
                           selected_customer_id=selected_customer_id,
                           month=month, payments=payments, customer=customer, company=company)

@reports_bp.route('/ar-aging')
@login_required
@role_required('Admin', 'Accountant')
def ar_aging():
    """Generates an Accounts Receivable Aging report."""
    today = date.today()
    invoices = ARInvoice.query.filter(ARInvoice.status != 'Paid').all()
    
    aging_data = {
        'current': [], '1-30': [], '31-60': [], '61-90': [], '91+': []
    }
    totals = { 'current': 0, '1-30': 0, '31-60': 0, '61-90': 0, '91+': 0, 'total': 0 }

    for inv in invoices:
        due_date = inv.date.date() # Convert datetime to date
        age = (today - due_date).days
        balance = inv.total - inv.paid
        totals['total'] += balance

        if age <= 0:
            aging_data['current'].append(inv)
            totals['current'] += balance
        elif 1 <= age <= 30:
            aging_data['1-30'].append(inv)
            totals['1-30'] += balance
        elif 31 <= age <= 60:
            aging_data['31-60'].append(inv)
            totals['31-60'] += balance
        elif 61 <= age <= 90:
            aging_data['61-90'].append(inv)
            totals['61-90'] += balance
        else:
            aging_data['91+'].append(inv)
            totals['91+'] += balance
            
    return render_template('ar_aging.html', aging_data=aging_data, totals=totals)

@reports_bp.route('/ap-aging')
@login_required
@role_required('Admin', 'Accountant')
def ap_aging():
    """Generates an Accounts Payable Aging report."""
    today = date.today()
    invoices = APInvoice.query.filter(APInvoice.status != 'Paid').all()

    aging_data = {
        'current': [], '1-30': [], '31-60': [], '61-90': [], '91+': []
    }
    totals = { 'current': 0, '1-30': 0, '31-60': 0, '61-90': 0, '91+': 0, 'total': 0 }

    for inv in invoices:
        due_date = inv.date.date()
        age = (today - due_date).days
        balance = inv.total - inv.paid
        totals['total'] += balance

        if age <= 0:
            aging_data['current'].append(inv)
            totals['current'] += balance
        elif 1 <= age <= 30:
            aging_data['1-30'].append(inv)
            totals['1-30'] += balance
        elif 31 <= age <= 60:
            aging_data['31-60'].append(inv)
            totals['31-60'] += balance
        elif 61 <= age <= 90:
            aging_data['61-90'].append(inv)
            totals['61-90'] += balance
        else:
            aging_data['91+'].append(inv)
            totals['91+'] += balance

    return render_template('ap_aging.html', aging_data=aging_data, totals=totals)

@reports_bp.route('/stock-card/<int:product_id>')
@login_required
@role_required('Admin', 'Accountant')
def stock_card(product_id):
    """Generates an inventory stock card for a specific product."""
    product = Product.query.get_or_404(product_id)
    
    sales = SaleItem.query.filter_by(product_id=product.id).all()
    purchases = PurchaseItem.query.filter_by(product_id=product.id).all()

    # --- ADD THIS QUERY ---
    adjustments = StockAdjustment.query.filter_by(product_id=product.id).all()
    # --- END ADD ---
    
    # Combine and sort transactions by date
    transactions = []
    for s in sales:
        transactions.append({
            'date': s.sale.created_at,
            'type': 'Sale',
            'ref_id': s.sale_id,
            'qty_in': 0,
            'qty_out': s.qty,
            'cost': product.cost_price # Use current cost for simplicity
        })
    for p in purchases:
         transactions.append({
            'date': p.purchase.created_at,
            'type': 'Purchase',
            'ref_id': p.purchase_id,
            'qty_in': p.qty,
            'qty_out': 0,
            'cost': p.unit_cost
        })

    # --- ADD THIS LOOP ---
    for adj in adjustments:
         transactions.append({
            'date': adj.created_at,
            'type': f'Adjustment ({adj.reason})',
            'ref_id': adj.id,
            'qty_in': adj.quantity_changed if adj.quantity_changed > 0 else 0,
            'qty_out': abs(adj.quantity_changed) if adj.quantity_changed < 0 else 0,
            'cost': product.cost_price 
        })
    # --- END ADD ---
        
    transactions.sort(key=lambda x: x['date'])
    
    # --- START OF FIX ---
    # Calculate the opening balance by working backward from the current quantity
    current_quantity = product.quantity
    
    total_sales_qty = sum(s.qty for s in sales)
    total_purchase_qty = sum(p.qty for p in purchases)
    total_adjustment_qty = sum(adj.quantity_changed for adj in adjustments)
    
    # Opening Balance = Current Qty - (all INs) + (all OUTs)
    opening_balance = current_quantity - total_purchase_qty - total_adjustment_qty + total_sales_qty
    
    # Set the starting running_balance to the calculated opening balance
    running_balance = opening_balance

    # Create a new list to hold transactions *with* the opening balance
    report_transactions = []
    
    # Add the Opening Balance as the first row in the report
    # We find the earliest transaction date (or use today) to put it first
    first_transaction_date = transactions[0]['date'] if transactions else datetime.utcnow()
    report_transactions.append({
        'date': first_transaction_date - timedelta(seconds=1), # Ensure it's the very first entry
        'type': 'Opening Balance',
        'ref_id': 'N/A',
        'qty_in': opening_balance if opening_balance > 0 else 0,
        'qty_out': abs(opening_balance) if opening_balance < 0 else 0,
        'cost': product.cost_price,
        'balance': running_balance
    })
    
    # Now, calculate the running balance for all other transactions
    for t in transactions:
        running_balance += t['qty_in'] - t['qty_out']
        t['balance'] = running_balance
        report_transactions.append(t)
    # --- END OF FIX ---
        
    return render_template('stock_card.html', product=product, 
                           transactions=report_transactions)


@reports_bp.route('/export/balance-sheet')
@login_required
@role_required('Admin', 'Accountant')
def export_balance_sheet():
    """Exports the balance sheet to CSV."""
    
    # --- ADD THIS BLOCK TO READ THE DATE FILTER ---
    # Balance Sheet is "As of" an end_date
    default_end_date = datetime.utcnow().strftime('%Y-%m-%d')
    end_date_str = request.args.get('end_date', default_end_date)
    end_date = parse_date(end_date_str)
    # --- END OF ADDED BLOCK ---

    # --- MODIFIED: Pass the end_date to the aggregator ---
    agg = aggregate_account_balances(start_date=None, end_date=end_date)
    assets, liabilities, equity = [], [], []

    # --- Re-run the balance_sheet logic ---
    for acc_code, bal in agg.items():
        acct_rec = Account.query.filter_by(code=acc_code).first()
        if not acct_rec: continue
        
        acc_name = acct_rec.name
        acc_type = acct_rec.type

        if acc_type == 'Asset':
            assets.append((acc_name, bal))
        elif acc_type == 'Liability':
            liabilities.append((acc_name, -bal))
        elif acc_type == 'Equity':
            equity.append((acc_name, -bal))

    # --- MODIFIED: We must also filter the Net Income calculation ---
    is_agg_net_income = aggregate_account_balances(start_date=None, end_date=end_date)
    revenues = {code: -bal for code, bal in is_agg_net_income.items() if Account.query.filter_by(code=code, type='Revenue').first()}
    expenses = {code: bal for code, bal in is_agg_net_income.items() if Account.query.filter_by(code=code, type='Expense').first()}
    # --- END MODIFICATION ---

    total_revenue = sum(revenues.values())
    total_expense = sum(expenses.values())
    net_income = total_revenue - total_expense
    
    equity.append(("Current Period Net Income", net_income))
    
    total_assets = sum(b for a, b in assets)
    total_liabilities = sum(b for a, b in liabilities)
    total_equity = sum(b for a, b in equity)
    total_liabilities_and_equity = total_liabilities + total_equity
    # --- End of logic ---

    output = io.StringIO()
    writer = csv.writer(output)
    
    # --- MODIFIED: Add the "As of" date to the report ---
    writer.writerow([f"Balance Sheet as of {end_date_str}", ""])
    writer.writerow([])
    # --- END MODIFICATION ---

    writer.writerow(["ASSETS", "Amount"])
    for name, balance in assets:
        writer.writerow([name, f"{balance:.2f}"])
    writer.writerow(["TOTAL ASSETS", f"{total_assets:.2f}"])
    writer.writerow([])
    
    writer.writerow(["LIABILITIES", "Amount"])
    for name, balance in liabilities:
        writer.writerow([name, f"{balance:.2f}"])
    writer.writerow(["TOTAL LIABILITIES", f"{total_liabilities:.2f}"])
    writer.writerow([])
    
    writer.writerow(["EQUITY", "Amount"])
    for name, balance in equity:
        writer.writerow([name, f"{balance:.2f}"])
    writer.writerow(["TOTAL EQUITY", f"{total_equity:.2f}"])
    writer.writerow([])
    
    writer.writerow(["TOTAL LIABILITIES & EQUITY", f"{total_liabilities_and_equity:.2f}"])

    output.seek(0)
    # --- MODIFIED: Include date in filename ---
    filename = f"balance_sheet_as_of_{end_date_str}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@reports_bp.route('/export/income-statement')
@login_required
@role_required('Admin', 'Accountant')
def export_income_statement():
    """Exports the income statement to CSV."""
    
    # --- ADD THIS BLOCK TO READ DATE FILTERS ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    # --- END OF ADDED BLOCK ---
    
    # --- MODIFIED: Pass dates to the aggregator ---
    agg = aggregate_account_balances(start_date, end_date)
    
    # --- Re-run the income_statement logic ---
    revenues, expenses = {}, {}
    for acc_code, bal in agg.items():
        acct_rec = Account.query.filter_by(code=acc_code).first()
        if not acct_rec: continue
        
        if acct_rec.type == 'Revenue':
            revenues[acct_rec.name] = -bal
        elif acct_rec.type == 'Expense':
            expenses[acct_rec.name] = bal

    total_revenue = sum(revenues.values())
    total_expense = sum(expenses.values())
    net_income = total_revenue - total_expense
    # --- End of logic ---
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # --- MODIFIED: Add date range to report ---
    date_range_label = f"For the period {start_date_str} to {end_date_str}"
    if not start_date_str or not end_date_str:
        date_range_label = "For All Time" # Fallback
    writer.writerow(["Income Statement", ""])
    writer.writerow([date_range_label, ""])
    writer.writerow([])
    # --- END MODIFICATION ---
    
    writer.writerow(["REVENUES", "Amount"])
    for name, balance in revenues.items():
        writer.writerow([name, f"{balance:.2f}"])
    writer.writerow(["Total Revenue", f"{total_revenue:.2f}"])
    writer.writerow([])
    
    writer.writerow(["EXPENSES", "Amount"])
    for name, balance in expenses.items():
        writer.writerow([name, f"{balance:.2f}"])
    writer.writerow(["Total Expenses", f"{total_expense:.2f}"])
    writer.writerow([])
    
    writer.writerow(["NET INCOME", f"{net_income:.2f}"])

    output.seek(0)
    filename = f"income_statement_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})



@reports_bp.route('/export/vat-report')
@login_required
@role_required('Admin', 'Accountant')
def export_vat_report():
    start_date = parse_date(request.args.get("start_date"))
    end_date = parse_date(request.args.get("end_date"))

    sale_query = Sale.query
    ar_invoice_query = ARInvoice.query
    purchase_query = Purchase.query
    ap_invoice_query = APInvoice.query

    if start_date:
        sale_query = sale_query.filter(Sale.created_at >= start_date)
        ar_invoice_query = ar_invoice_query.filter(ARInvoice.date >= start_date)
        purchase_query = purchase_query.filter(Purchase.created_at >= start_date)
        ap_invoice_query = ap_invoice_query.filter(APInvoice.date >= start_date)
    if end_date:
        end_date_inclusive = end_date + timedelta(days=1)
        sale_query = sale_query.filter(Sale.created_at < end_date_inclusive)
        ar_invoice_query = ar_invoice_query.filter(ARInvoice.date < end_date_inclusive)
        purchase_query = purchase_query.filter(Purchase.created_at < end_date_inclusive)
        ap_invoice_query = ap_invoice_query.filter(APInvoice.date < end_date_inclusive)

    sales_vat = float(sale_query.filter(Sale.is_vatable == True).with_entities(func.coalesce(func.sum(Sale.vat), 0)).scalar() or 0)
    ar_invoice_vat = float(ar_invoice_query.filter(ARInvoice.vat != None, ARInvoice.vat > 0).with_entities(func.coalesce(func.sum(ARInvoice.vat), 0)).scalar() or 0)
    total_output_vat = sales_vat + ar_invoice_vat

    purchases_vat = float(purchase_query.filter(Purchase.is_vatable == True).with_entities(func.coalesce(func.sum(Purchase.vat), 0)).scalar() or 0)
    ap_invoice_vat = float(ap_invoice_query.filter(APInvoice.vat != None, APInvoice.vat > 0).with_entities(func.coalesce(func.sum(APInvoice.vat), 0)).scalar() or 0)
    total_input_vat = purchases_vat + ap_invoice_vat

    nonvat_sales = float(sale_query.filter((Sale.is_vatable == False) | (Sale.is_vatable == None)).with_entities(func.coalesce(func.sum(Sale.total), 0)).scalar() or 0)
    nonvat_ar = float(ar_invoice_query.filter((ARInvoice.vat == 0) | (ARInvoice.vat == None)).with_entities(func.coalesce(func.sum(ARInvoice.total), 0)).scalar() or 0)
    total_nonvat_sales = nonvat_sales + nonvat_ar

    nonvat_purchases = float(purchase_query.filter((Purchase.is_vatable == False) | (Purchase.is_vatable == None)).with_entities(func.coalesce(func.sum(Purchase.total), 0)).scalar() or 0)
    nonvat_ap = float(ap_invoice_query.filter((APInvoice.vat == 0) | (APInvoice.vat == None)).with_entities(func.coalesce(func.sum(APInvoice.total), 0)).scalar() or 0)
    total_nonvat_purchases = nonvat_purchases + nonvat_ap

    vat_payable = total_output_vat - total_input_vat

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Type", "Amount (₱)"])
    writer.writerow(["Total Input VAT (from all vatable purchases)", f"{total_input_vat:.2f}"])
    writer.writerow(["Total Output VAT (from all vatable sales)", f"{total_output_vat:.2f}"])
    writer.writerow(["VAT Payable", f"{vat_payable:.2f}"])
    writer.writerow([])

    # Add Non-VAT details
    writer.writerow(["Non-VAT Sales (Cash + AR)", f"{total_nonvat_sales:.2f}"])
    writer.writerow(["Non-VAT Purchases (Cash + AP)", f"{total_nonvat_purchases:.2f}"])

    output.seek(0)
    filename = f"vat_report_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@reports_bp.route('/export/trial-balance')
@login_required
@role_required('Admin', 'Accountant')
def export_trial_balance():
    """Exports the trial balance to CSV."""
    
    # --- ADD THIS BLOCK TO READ DATE FILTERS ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    # --- END OF ADDED BLOCK ---

    # --- MODIFIED: Pass dates to the aggregator ---
    agg = aggregate_account_balances(start_date, end_date)
    
    # --- Re-run the trial_balance logic ---
    tb = []
    total_debit = 0.0
    total_credit = 0.0
    for acc_code, val in agg.items():
        acc_details = Account.query.filter_by(code=acc_code).first()
        acc_name = acc_details.name if acc_details else f"Unknown ({acc_code})"
        
        if val >= 0:
            tb.append({'code': acc_code, 'name': acc_name, 'debit': val, 'credit': 0.0})
            total_debit += val
        else:
            tb.append({'code': acc_code, 'name': acc_name, 'debit': 0.0, 'credit': -val})
            total_credit += -val
    tb.sort(key=lambda x: x['code'])
    # --- End of logic ---
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # --- MODIFIED: Add date range to report ---
    date_range_label = f"For the period {start_date_str} to {end_date_str}"
    if not start_date_str or not end_date_str:
        date_range_label = "For All Time" # Fallback
    writer.writerow(["Trial Balance", ""])
    writer.writerow([date_range_label, "", ""])
    writer.writerow([])
    # --- END MODIFICATION ---
    
    writer.writerow(["Code", "Account Name", "Debit", "Credit"])
    for row in tb:
        writer.writerow([row['code'], row['name'], f"{row['debit']:.2f}", f"{row['credit']:.2f}"])
    writer.writerow([])
    writer.writerow(["Totals", "", f"{total_debit:.2f}", f"{total_credit:.2f}"])

    output.seek(0)
    filename = f"trial_balance_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


    # --- MODIFIED: The core function now accepts dates ---
def aggregate_account_balances(start_date=None, end_date=None):
    """
    Return dict: account_code -> balance (debit - credit) for a given date range.
    """
    agg = defaultdict(float)
    
    # --- MODIFIED: Create a base query ---
    query = JournalEntry.query

    # --- MODIFIED: Apply date filters if they exist ---
    if start_date:
        query = query.filter(JournalEntry.created_at >= start_date)
    if end_date:
        # Add one day to the end_date to make the filter inclusive
        end_date_inclusive = end_date + timedelta(days=1)
        query = query.filter(JournalEntry.created_at < end_date_inclusive)

    # --- MODIFIED: Execute the filtered query ---
    for je in query.all():
        for line in je.entries():
            acc_code = line.get('account_code') 
            if not acc_code:
                continue 
                
            debit = float(line.get('debit', 0) or 0)
            credit = float(line.get('credit', 0) or 0)
            agg[acc_code] += debit - credit
    return dict(agg)


@reports_bp.route('/general-ledger')
@login_required
@role_required('Admin', 'Accountant')
def general_ledger():
    # --- NEW: Get dates from URL ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')

    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    
    # --- MODIFIED: Pass dates to the aggregator ---
    # The aggregator is designed to work with dates now.
    agg = aggregate_account_balances(start_date, end_date)
    
    gl_data = []
    
    # This logic remains the same (using Trial Balance rules to show the net effect)
    for acc_code, balance in agg.items():
        acc_details = Account.query.filter_by(code=acc_code).first()
        if not acc_details:
            continue

        is_debit_account = acc_details.type in ['Asset', 'Expense']
        
        if is_debit_account:
            # Positive balance is net Debit, Negative balance is net Credit
            final_debit = balance if balance >= 0 else 0.0
            final_credit = abs(balance) if balance < 0 else 0.0
            balance_type = 'Debit'
        else:
            # Negative balance is net Credit, Positive balance is net Debit
            final_debit = abs(balance) if balance > 0 else 0.0
            final_credit = abs(balance) if balance <= 0 else 0.0
            balance_type = 'Credit'

        gl_data.append({
            'account': f"{acc_code} - {acc_details.name}",
            'debit': final_debit, 
            'credit': final_credit,
            'balance': abs(balance),
            'balance_type': balance_type
        })
        
    gl_data.sort(key=lambda x: x['account']) # Sort by account name/code
    
    # --- MODIFIED: Pass dates to the template ---
    return render_template('general_ledger.html', 
                           gl_data=gl_data,
                           start_date=start_date_str, 
                           end_date=end_date_str)


@reports_bp.route('/export/general-ledger')
@login_required
@role_required('Admin', 'Accountant')
def export_general_ledger():
    """Exports the General Ledger Summary to CSV."""
    
    # --- NEW: Get dates from URL ---
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)

    # --- MODIFIED: Pass dates to the aggregator ---
    agg = aggregate_account_balances(start_date, end_date)
    
    gl_data = []
    
    # This logic remains the same
    for acc_code, balance in agg.items():
        acc_details = Account.query.filter_by(code=acc_code).first()
        if not acc_details:
            continue

        is_debit_account = acc_details.type in ['Asset', 'Expense']
        
        if is_debit_account:
            final_debit = balance if balance >= 0 else 0.0
            final_credit = abs(balance) if balance < 0 else 0.0 
            balance_type = 'Debit'
        else:
            final_debit = abs(balance) if balance > 0 else 0.0
            final_credit = abs(balance) if balance <= 0 else 0.0
            balance_type = 'Credit'

        gl_data.append({
            'account': f"{acc_code} - {acc_details.name}",
            'debit': final_debit, 
            'credit': final_credit,
            'balance': abs(balance),
            'balance_type': balance_type
        })
        
    gl_data.sort(key=lambda x: x['account'])

    output = io.StringIO()
    writer = csv.writer(output)
    
    # --- NEW: Add date range to the CSV header ---
    date_range_label = f"For the period {start_date_str} to {end_date_str}"
    if not start_date_str or not end_date_str:
        date_range_label = "For All Time (Current Balances)" # Fallback label
        
    writer.writerow(["General Ledger Summary", ""])
    writer.writerow([date_range_label, ""])
    writer.writerow([])
    # --- END NEW ---
    
    writer.writerow(["Account", "Net Debits (₱)", "Net Credits (₱)", "Balance (₱)", "Balance Type"])
    total_debits = 0.0
    total_credits = 0.0
    
    for row in gl_data:
        total_debits += row['debit']
        total_credits += row['credit']
        writer.writerow([
            row['account'], 
            f"{row['debit']:.2f}", 
            f"{row['credit']:.2f}", 
            f"{row['balance']:.2f}", 
            row['balance_type']
        ])
    
    writer.writerow([])
    writer.writerow(["TOTALS (Net Balances)", f"{total_debits:.2f}", f"{total_credits:.2f}", "", ""])

    output.seek(0)
    # --- NEW: Include date range in the filename for better file management ---
    date_suffix = f"{start_date_str.replace('-', '')}_{end_date_str.replace('-', '')}" if start_date_str and end_date_str else datetime.now().strftime('%Y%m%d')
    filename = f"general_ledger_summary_{date_suffix}.csv"
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})
