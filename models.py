from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import json

db = SQLAlchemy()

class CompanyProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    business_style = db.Column(db.String(200))
    tin = db.Column(db.String(50), nullable=False)
    address = db.Column(db.String(300), nullable=False)
    license_key = db.Column(db.String(100))
    next_or_number = db.Column(db.Integer, default=1)
    next_si_number = db.Column(db.Integer, default=1)
    next_invoice_number = db.Column(db.Integer, default=1)

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # Asset, Liability, Equity, Revenue, Expense
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    sale_price = db.Column(db.Float, nullable=False, default=0.0)
    cost_price = db.Column(db.Float, nullable=False, default=0.0)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    LOW_STOCK_THRESHOLD = 5

    def is_low_stock(self):
        return self.quantity <= self.LOW_STOCK_THRESHOLD

    def to_dict(self):
        return {"id": self.id, "sku": self.sku, "name": self.name, "sale_price": self.sale_price, "cost_price": self.cost_price, "quantity": self.quantity, "low": self.is_low_stock()}

    def adjust_stock(self, change):
        self.quantity = max(self.quantity + change, 0)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer_name = db.Column(db.String(200), nullable=True)
    total = db.Column(db.Float, nullable=False)
    vat = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(50), default='paid')
    items = db.relationship('SaleItem', backref='sale', cascade='all, delete-orphan')
    document_number = db.Column(db.String(50), unique=True)
    document_type = db.Column(db.String(10)) # To store 'OR' or 'SI'

class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200))
    sku = db.Column(db.String(64))
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False)
    cogs = db.Column(db.Float, nullable=False, default=0.0)

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    supplier = db.Column(db.String(200))
    total = db.Column(db.Float, nullable=False, default=0.0)
    vat = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(50), default='Recorded', nullable=False)
    items = db.relationship('PurchaseItem', backref='purchase', cascade='all, delete-orphan')

class PurchaseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey('purchase.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200))
    sku = db.Column(db.String(64))
    qty = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False)

class JournalEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(400))
    entries_json = db.Column(db.Text)

    def entries(self):
        try:
            return json.loads(self.entries_json)
        except (json.JSONDecodeError, TypeError):
            return []

# ✅ --- FIX: Inherits from UserMixin to integrate with Flask-Login ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='Cashier')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    tin = db.Column(db.String(50))
    address = db.Column(db.String(300))
    wht_rate_percent = db.Column(db.Float, default=0.0)

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
    status = db.Column(db.String(50), default='Open')

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
    ref_type = db.Column(db.String(20))
    ref_id = db.Column(db.Integer)
    method = db.Column(db.String(50))
    wht_amount = db.Column(db.Float, default=0.0)

class CreditMemo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    ar_invoice_id = db.Column(db.Integer, db.ForeignKey('ar_invoice.id'), nullable=True)
    reason = db.Column(db.String(300))
    amount_net = db.Column(db.Float, nullable=False)
    vat = db.Column(db.Float, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    # Relationships
    customer = db.relationship('Customer')
    ar_invoice = db.relationship('ARInvoice')

class StockAdjustment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product = db.relationship('Product')
    quantity_changed = db.Column(db.Integer, nullable=False) # e.g., -5 for loss, 10 for found
    reason = db.Column(db.String(255), nullable=False) # e.g., 'Spoilage', 'Physical Count Correction'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User')
    action = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(45)) # To store user's IP

    def __repr__(self):
        username = self.user.username if self.user else 'System'
        return f'<AuditLog {self.timestamp} - {username}: {self.action}>'