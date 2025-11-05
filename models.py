# models.py update

# Assuming this is around line 134

class ARInvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ar_invoice_id = db.Column(db.Integer, db.ForeignKey('ar_invoice.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product_name = db.Column(db.String(200))
    sku = db.Column(db.String(64))
    qty = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False)
    cogs = db.Column(db.Float, nullable=False, default=0.0)
    is_vatable = db.Column(db.Boolean, nullable=False, default=True)

# Update ARInvoice class to add new fields

class ARInvoice(db.Model):
    # Existing fields...
    is_vatable = db.Column(db.Boolean, nullable=False, default=True)
    invoice_number = db.Column(db.String(50), unique=True, nullable=True)
    description = db.Column(db.String(400))
    items = db.relationship('ARInvoiceItem', backref='ar_invoice', cascade='all, delete-orphan')
  

# Previous code remains unchanged