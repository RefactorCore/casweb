from flask import Blueprint, render_template, request
from flask_login import login_required
from models import JournalEntry, Account, Sale, Purchase, Product, ARInvoice, APInvoice
from collections import defaultdict
import json

reports_bp = Blueprint('reports', __name__, url_prefix='')


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
@login_required
def purchases():
    purchases = Purchase.query.order_by(Purchase.created_at.desc()).all()
    return render_template('purchases.html', purchases=purchases)
