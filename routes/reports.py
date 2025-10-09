from flask import Blueprint, render_template, request
from flask_login import login_required
# Add CompanyProfile, Customer, Supplier, CreditMemo
from models import db, JournalEntry, Account, Sale, Purchase, Product, ARInvoice, APInvoice, CompanyProfile, Customer, Supplier, CreditMemo, Payment
from collections import defaultdict
import json
from sqlalchemy import func, extract
from datetime import datetime
from routes.decorators import role_required

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')


def aggregate_account_balances():
    """Return dict: account_name -> balance (debit - credit)."""
    agg = defaultdict(float)
    for je in JournalEntry.query.all():
        for line in je.entries():
            acc = line.get('account')
            debit = float(line.get('debit', 0) or 0)
            credit = float(line.get('credit', 0) or 0)
            agg[acc] += debit - credit
    return dict(agg)


@reports_bp.route('/trial-balance')
@login_required
@role_required('Admin', 'Accountant')
def trial_balance():
    agg = aggregate_account_balances()
    tb = []
    total_debit = 0.0
    total_credit = 0.0
    for acc, val in agg.items():
        if val >= 0:
            tb.append({'account': acc, 'debit': val, 'credit': 0.0})
            total_debit += val
        else:
            tb.append({'account': acc, 'debit': 0.0, 'credit': -val})
            total_credit += -val
    return render_template('trial_balance.html', tb=tb, total_debit=total_debit, total_credit=total_credit)


@reports_bp.route('/ledger/<account>')
@login_required
@role_required('Admin', 'Accountant')
def ledger(account):
    rows = []
    balance = 0.0
    for je in JournalEntry.query.order_by(JournalEntry.created_at).all():
        for line in je.entries():
            if line.get('account') == account:
                debit = float(line.get('debit', 0) or 0)
                credit = float(line.get('credit', 0) or 0)
                balance += debit - credit
                rows.append({'date': je.created_at, 'desc': je.description, 'debit': debit, 'credit': credit, 'balance': balance})
    return render_template('ledger.html', account=account, rows=rows, balance=balance)


@reports_bp.route('/balance-sheet')
@login_required
@role_required('Admin', 'Accountant')
def balance_sheet():
    agg = aggregate_account_balances()
    assets, liabilities, equity = [], [], []
    for acc, bal in agg.items():
        acct_rec = Account.query.filter_by(name=acc).first()
        typ = acct_rec.type if acct_rec else None
        if typ == 'Asset' or (typ is None and bal >= 0):
            assets.append((acc, bal if bal >= 0 else 0.0))
        elif typ == 'Liability':
            liabilities.append((acc, -bal if bal < 0 else bal))
        elif typ == 'Equity':
            equity.append((acc, bal))
        else:
            # fallback simple rule
            if bal >= 0:
                assets.append((acc, bal))
            else:
                liabilities.append((acc, -bal))

    total_assets = sum(b for a, b in assets)
    total_liabilities = sum(b for a, b in liabilities)
    total_equity = sum(b for a, b in equity)
    return render_template('balance_sheet.html', assets=assets, liabilities=liabilities, equity=equity,
                           total_assets=total_assets, total_liabilities=total_liabilities, total_equity=total_equity)


@reports_bp.route('/income-statement')
@login_required
@role_required('Admin', 'Accountant')
def income_statement():
    agg = aggregate_account_balances()
    # Identify revenues and expenses heuristically
    revenues = {k: -v for k, v in agg.items() if 'Sales' in k or 'Revenue' in k}
    expenses = {k: v for k, v in agg.items() if 'COGS' in k or 'Expense' in k or 'Cost' in k}
    total_revenue = sum(revenues.values())
    total_expense = sum(expenses.values())
    net_income = total_revenue - total_expense
    return render_template('income_statement.html', revenues=revenues, expenses=expenses,
                           total_revenue=total_revenue, total_expense=total_expense, net_income=net_income)


@reports_bp.route('/vat-report')
@login_required
@role_required('Admin', 'Accountant')
def vat_report():
    sales = Sale.query.all()
    total_sales = sum(s.total - s.vat for s in sales)
    total_vat = sum(s.vat for s in sales)
    journals = JournalEntry.query.order_by(JournalEntry.created_at.desc()).all()
    return render_template('vat_report.html', total_sales=total_sales, total_vat=total_vat, journals=journals)


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
    
    # Adjustments to Output Tax (from Credit Memos)
    returns_in_month = CreditMemo.query.filter(
        extract('year', CreditMemo.date) == year,
        extract('month', CreditMemo.date) == month_num
    ).all()

    total_sales_net = sum(s.total - s.vat for s in sales_in_month)
    total_output_vat = sum(s.vat for s in sales_in_month)
    
    total_returns_net = sum(cm.amount_net for cm in returns_in_month)
    total_returns_vat = sum(cm.vat for cm in returns_in_month)

    # Input Tax (from Purchases)
    purchases_in_month = APInvoice.query.filter(
        extract('year', APInvoice.date) == year,
        extract('month', APInvoice.date) == month_num
    ).all()
    total_purchases_net = sum(p.total - p.vat for p in purchases_in_month)
    total_input_vat = sum(p.vat for p in purchases_in_month)
    
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