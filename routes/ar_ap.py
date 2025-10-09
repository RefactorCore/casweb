from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from models import db, Customer, Supplier, ARInvoice, APInvoice, Payment, JournalEntry, CreditMemo 
import io, csv
import json
from .decorators import role_required

ar_ap_bp = Blueprint('ar_ap', __name__, url_prefix='')


@ar_ap_bp.route('/customers', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant')
def customers():
    if request.method == 'POST':
        name = request.form.get('name')
        tin = request.form.get('tin')
        addr = request.form.get('address')
        if not name:
            flash('Customer name is required')
            return redirect(url_for('ar_ap.customers'))
        c = Customer(name=name, tin=tin, address=addr)
        db.session.add(c)
        db.session.commit()
        flash('Customer added')
        return redirect(url_for('ar_ap.customers'))
    custs = Customer.query.order_by(Customer.name).all()
    return render_template('customers.html', customers=custs)


@ar_ap_bp.route('/suppliers', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant')
def suppliers():
    if request.method == 'POST':
        name = request.form.get('name')
        tin = request.form.get('tin')
        addr = request.form.get('address')
        if not name:
            flash('Supplier name is required')
            return redirect(url_for('ar_ap.suppliers'))
        s = Supplier(name=name, tin=tin, address=addr)
        db.session.add(s)
        db.session.commit()
        flash('Supplier added')
        return redirect(url_for('ar_ap.suppliers'))
    sups = Supplier.query.order_by(Supplier.name).all()
    return render_template('suppliers.html', suppliers=sups)


@ar_ap_bp.route('/ar-invoices', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant')
def ar_invoices():
    """
    Create AR invoice (credit sale). Creates a JournalEntry:
      Debit Accounts Receivable (net + vat)
      Credit Sales Revenue (net)
      Credit VAT Payable (vat)
    """
    if request.method == 'POST':
        try:
            cust_id = int(request.form.get('customer_id') or 0) or None
        except ValueError:
            cust_id = None
        total = float(request.form.get('total') or 0)
        vat = float(request.form.get('vat') or 0)
        if total <= 0:
            flash('Invoice total must be > 0')
            return redirect(url_for('ar_ap.ar_invoices'))
        inv = ARInvoice(customer_id=cust_id, total=round(total, 2), vat=round(vat, 2))
        db.session.add(inv)
        db.session.flush()

        # Journal entry
        je_lines = [
            {'account': 'Accounts Receivable', 'debit': round(inv.total, 2), 'credit': 0},
            {'account': 'Sales Revenue', 'debit': 0, 'credit': round(inv.total - inv.vat, 2)},
            {'account': 'VAT Payable', 'debit': 0, 'credit': round(inv.vat, 2)},
        ]
        je = JournalEntry(description=f'AR Invoice #{inv.id}', entries_json=json.dumps(je_lines))
        db.session.add(je)
        db.session.commit()
        flash('AR Invoice created and journal entry recorded.')
        return redirect(url_for('ar_ap.ar_invoices'))

    invoices = ARInvoice.query.order_by(ARInvoice.date.desc()).all()
    customers = Customer.query.order_by(Customer.name).all()
    return render_template('ar_invoices.html', invoices=invoices, customers=customers)


@ar_ap_bp.route('/ap-invoices', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant')
def ap_invoices():
    """
    Create AP invoice (credit purchase). Creates a JournalEntry:
      Debit Inventory / Expense (net)
      Debit VAT Input (vat)
      Credit Accounts Payable (total)
    """
    if request.method == 'POST':
        try:
            sup_id = int(request.form.get('supplier_id') or 0) or None
        except ValueError:
            sup_id = None
        total = float(request.form.get('total') or 0)
        vat = float(request.form.get('vat') or 0)
        if total <= 0:
            flash('Invoice total must be > 0')
            return redirect(url_for('ar_ap.ap_invoices'))
        inv = APInvoice(supplier_id=sup_id, total=round(total, 2), vat=round(vat, 2))
        db.session.add(inv)
        db.session.flush()

        je_lines = [
            {'account': 'Inventory', 'debit': round(inv.total - inv.vat, 2), 'credit': 0},
            {'account': 'VAT Input', 'debit': round(inv.vat, 2), 'credit': 0},
            {'account': 'Accounts Payable', 'debit': 0, 'credit': round(inv.total, 2)},
        ]
        je = JournalEntry(description=f'AP Invoice #{inv.id}', entries_json=json.dumps(je_lines))
        db.session.add(je)
        db.session.commit()
        flash('AP Invoice created and journal entry recorded.')
        return redirect(url_for('ar_ap.ap_invoices'))

    invoices = APInvoice.query.order_by(APInvoice.date.desc()).all()
    suppliers = Supplier.query.order_by(Supplier.name).all()
    return render_template('ap_invoices.html', invoices=invoices, suppliers=suppliers)


@ar_ap_bp.route('/payment', methods=['POST'])
@login_required
def record_payment():
    """
    Record payment for AR or AP and create corresponding journal entry.
      AR payment: Debit Cash, Debit CWT, Credit Accounts Receivable
      AP payment: Debit Accounts Payable, Credit Cash
    """
    ref_type = request.form.get('ref_type')
    try:
        ref_id = int(request.form.get('ref_id') or 0)
    except ValueError:
        flash('Invalid reference id'); return redirect(url_for('ar_ap.customers'))
    amount = float(request.form.get('amount') or 0)
    method = request.form.get('method') or 'Cash'
    wht_amount = 0.0 # Withholding Tax

    if amount <= 0:
        flash('Amount must be > 0'); return redirect(url_for('ar_ap.customers'))

    if ref_type == 'AR':
        inv = ARInvoice.query.get(ref_id)
        if inv and inv.customer and inv.customer.wht_rate_percent > 0:
            # Calculate Withholding Tax based on amount net of VAT
            wht_base = (amount / 1.12) # Assuming 12% VAT
            wht_amount = round(wht_base * (inv.customer.wht_rate_percent / 100.0), 2)
        
        if inv:
            inv.paid += (amount + wht_amount)
            inv.status = 'Paid' if inv.paid >= inv.total else 'Partially Paid'
            
        # JE: Debit Cash, Debit CWT, Credit Accounts Receivable
        je_lines = [
            {'account': 'Cash', 'debit': round(amount, 2), 'credit': 0},
            {'account': 'Creditable Withholding Tax', 'debit': round(wht_amount, 2), 'credit': 0},
            {'account': 'Accounts Receivable', 'debit': 0, 'credit': round(amount + wht_amount, 2)}
        ]
        
    elif ref_type == 'AP':
        inv = APInvoice.query.get(ref_id)
        if inv:
            inv.paid += amount
            inv.status = 'Paid' if inv.paid >= inv.total else 'Partially Paid'
        # JE: Debit Accounts Payable, Credit Cash
        je_lines = [
            {'account': 'Accounts Payable', 'debit': round(amount, 2), 'credit': 0},
            {'account': 'Cash', 'debit': 0, 'credit': round(amount, 2)}
        ]
    else:
        flash('Unknown ref type'); db.session.rollback(); return redirect(url_for('ar_ap.customers'))

    # Save the payment record
    p = Payment(amount=round(amount, 2), ref_type=ref_type, ref_id=ref_id, method=method, wht_amount=wht_amount)
    db.session.add(p)

    je = JournalEntry(description=f'Payment for {ref_type} #{ref_id}', entries_json=json.dumps(je_lines))
    db.session.add(je)
    db.session.commit()
    flash('Payment recorded and journal entry created.')
    # Redirect based on where payment was made from
    return redirect(request.referrer or url_for('ar_ap.ar_invoices'))

# --- ADD THIS NEW ROUTE ---
@ar_ap_bp.route('/credit-memos', methods=['GET', 'POST'])
@login_required
@role_required('Admin', 'Accountant')
def credit_memos():
    """
    Create a Credit Memo for a sales return/allowance.
    Journal Entry:
      Debit Sales Returns
      Debit VAT Payable
      Credit Accounts Receivable
    """
    if request.method == 'POST':
        customer_id = int(request.form.get('customer_id'))
        ar_invoice_id = int(request.form.get('ar_invoice_id') or 0) or None
        reason = request.form.get('reason')
        total_amount = float(request.form.get('total_amount') or 0)

        if not customer_id or total_amount <= 0:
            flash('Customer and a valid amount are required.', 'danger')
            return redirect(url_for('ar_ap.credit_memos'))

        # Calculate net and VAT (assuming 12% VAT)
        amount_net = round(total_amount / 1.12, 2)
        vat = round(total_amount - amount_net, 2)

        cm = CreditMemo(
            customer_id=customer_id,
            ar_invoice_id=ar_invoice_id,
            reason=reason,
            amount_net=amount_net,
            vat=vat,
            total_amount=total_amount
        )
        db.session.add(cm)
        db.session.flush()

        # Adjust original invoice if linked
        if ar_invoice_id:
            inv = ARInvoice.query.get(ar_invoice_id)
            if inv:
                # This is a simplification; a real system might apply the credit to payments.
                # For now, we reduce the total.
                inv.total = max(0, inv.total - total_amount)

        # Journal Entry
        je_lines = [
            {'account': 'Sales Returns', 'debit': amount_net, 'credit': 0},
            {'account': 'VAT Payable', 'debit': vat, 'credit': 0}, # Debit to reduce liability
            {'account': 'Accounts Receivable', 'debit': 0, 'credit': total_amount}
        ]
        je = JournalEntry(description=f'Credit Memo #{cm.id} for {reason}', entries_json=json.dumps(je_lines))
        db.session.add(je)
        db.session.commit()
        flash('Credit Memo created successfully.', 'success')
        return redirect(url_for('ar_ap.credit_memos'))

    memos = CreditMemo.query.order_by(CreditMemo.date.desc()).all()
    customers = Customer.query.order_by(Customer.name).all()
    invoices = ARInvoice.query.filter(ARInvoice.status != 'Paid').order_by(ARInvoice.id.desc()).all()
    return render_template('credit_memos.html', memos=memos, customers=customers, invoices=invoices)


@ar_ap_bp.route('/export/ar.csv')
@login_required
def export_ar_csv():
    invoices = ARInvoice.query.order_by(ARInvoice.date.desc()).all()
    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=['id', 'date', 'customer_id', 'total', 'vat', 'paid', 'status'])
    writer.writeheader()
    for inv in invoices:
        writer.writerow({
            'id': inv.id,
            'date': inv.date.strftime('%Y-%m-%d'),
            'customer_id': inv.customer_id or '',
            'total': f"{inv.total:.2f}",
            'vat': f"{inv.vat:.2f}",
            'paid': f"{inv.paid:.2f}",
            'status': inv.status
        })
    return send_file(io.BytesIO(si.getvalue().encode('utf-8')), mimetype='text/csv', download_name='ar_invoices.csv', as_attachment=True)


@ar_ap_bp.route('/export/ap.csv')
@login_required
def export_ap_csv():
    invoices = APInvoice.query.order_by(APInvoice.date.desc()).all()
    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=['id', 'date', 'supplier_id', 'total', 'vat', 'paid', 'status'])
    writer.writeheader()
    for inv in invoices:
        writer.writerow({
            'id': inv.id,
            'date': inv.date.strftime('%Y-%m-%d'),
            'supplier_id': inv.supplier_id or '',
            'total': f"{inv.total:.2f}",
            'vat': f"{inv.vat:.2f}",
            'paid': f"{inv.paid:.2f}",
            'status': inv.status
        })
    return send_file(io.BytesIO(si.getvalue().encode('utf-8')), mimetype='text/csv', download_name='ap_invoices.csv', as_attachment=True)
