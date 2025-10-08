from flask_sqlalchemy import SQLAlchemy
from datetime import datetime # Keep ONLY this import for datetime
import json
# REMOVE the second 'import datetime' line

db = SQLAlchemy()


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # Asset, Liability, Equity, Revenue, Expense
    # FIX: Change to datetime.utcnow, relying on 'from datetime import datetime'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def compute_account_balances():
    """Aggregate balances for each account from JournalEntry lines."""
    accts = {}
    # JournalEntry class is defined below, ensure it's imported or defined if this function runs early
    for je in JournalEntry.query.all():
        for line in je.entries():
            acc = line.get('account')
            debit = float(line.get('debit',0) or 0)
            credit = float(line.get('credit',0) or 0)
            if acc not in accts:
                accts[acc] = 0.0
            accts[acc] += debit - credit
    return accts

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    sale_price = db.Column(db.Float, nullable=False, default=0.0)
    cost_price = db.Column(db.Float, nullable=False, default=0.0)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    LOW_STOCK_THRESHOLD = 5  # 👈 adjustable stock warning

    def is_low_stock(self):
        """Return True if below threshold."""
        return self.quantity <= self.LOW_STOCK_THRESHOLD

    def to_dict(self):
        """For JSON APIs and frontend search."""
        return {
            "id": self.id,
            "sku": self.sku,
            "name": self.name,
            "sale_price": self.sale_price,
            "cost_price": self.cost_price,
            "quantity": self.quantity,
            "low": self.is_low_stock(),
        }

    def adjust_stock(self, change):
        """Adjust product stock by given qty (can be negative)."""
        self.quantity = max(self.quantity + change, 0)


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer_name = db.Column(db.String(200), nullable=True)  # 👈 Add this line
    total = db.Column(db.Float, nullable=False)
    vat = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(50), default='paid')  # 👈 Optional but useful
    items = db.relationship('SaleItem', backref='sale', cascade='all, delete-orphan')

class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200))
    sku = db.Column(db.String(64))
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)  # sale price
    line_total = db.Column(db.Float, nullable=False)
    cogs = db.Column(db.Float, nullable=False, default=0.0)  # cost of goods sold amount for the line

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    supplier = db.Column(db.String(200))
    total = db.Column(db.Float, nullable=False, default=0.0)
    vat = db.Column(db.Float, nullable=False, default=0.0)
    items = db.relationship('PurchaseItem', backref='purchase', cascade='all, delete-orphan')

    def to_dict(self):
        """Simple summary for dashboards or AJAX."""
        return {
            "id": self.id,
            "date": self.created_at.strftime("%Y-%m-%d"),
            "supplier": self.supplier or "Unknown",
            "total": round(self.total, 2),
            "vat": round(self.vat, 2),
        }

class PurchaseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey('purchase.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200))
    sku = db.Column(db.String(64))
    qty = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)  # cost per unit (net)
    line_total = db.Column(db.Float, nullable=False)

class JournalEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(400))
    entries_json = db.Column(db.Text)

    def entries(self):
        try:
            return json.loads(self.entries_json)
        except Exception:
            return []

    def pretty_summary(self):
        """Readable short format for accounting dashboard."""
        try:
            lines = self.entries()
            accounts = ", ".join(l["account"] for l in lines)
            return f"JE#{self.id}: {accounts}"
        except:
            return f"JE#{self.id}"


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='Cashier')  # Admin, Accountant, Cashier
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    tin = db.Column(db.String(50))
    address = db.Column(db.String(300))

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    tin = db.Column(db.String(50))
    address = db.Column(db.String(300))

class ARInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    customer = db.relationship('Customer')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    total = db.Column(db.Float, nullable=False)
    vat = db.Column(db.Float, nullable=False, default=0.0)
    paid = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(50), default='Open')  # Open, Paid, Partially Paid

class APInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)
    supplier = db.relationship('Supplier')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    total = db.Column(db.Float, nullable=False)
    vat = db.Column(db.Float, nullable=False, default=0.0)
    paid = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(50), default='Open')

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    ref_type = db.Column(db.String(20))  # 'AR' or 'AP'
    ref_id = db.Column(db.Integer)  # id of invoice
    method = db.Column(db.String(50))  # Cash, Bank transfer, etc.