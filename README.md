# PH-Approve Accounting System — Full Accounting MVP (with VAT & COGS)

This is an expanded Flask + SQLite accounting MVP that includes:
- Inventory monitoring with **cost price** and **average costing** for COGS
- POS (sales) that calculates **VAT (12%)**, records invoices and journal entries
- Purchase entry (buying inventory) which records VAT input and increases inventory & average cost
- Double-entry style **journal entries** stored as line arrays (account, debit, credit)
- VAT report (sales, VAT collected) and basic ledger/reporting pages

**Key accounting flows implemented (simplified for an MVP):**
1. **Sale (POS)** — when a sale is recorded:
   - Calculate `sales_net` (price * qty), `vat` (12% of sales_net), `total` = sales_net + vat
   - Reduce product quantity
   - Record journal entry with lines:
     - Debit Cash (total)
     - Credit Sales Revenue (sales_net)
     - Credit VAT Payable (vat)
     - Debit COGS (COGS amount)
     - Credit Inventory (COGS amount)
   - COGS uses product `cost_price` (average cost); inventory average cost is updated on purchases.

2. **Purchase (Inventory buy)** — when inventory is purchased:
   - Record purchase with `cost_price`, `qty`, `vat` on purchase (input VAT)
   - Increase product quantity
   - Update product average `cost_price` (weighted average)
   - Journal entry:
     - Debit Inventory (net)
     - Debit VAT Input (vat)
     - Credit Accounts Payable / Cash (total)

3. **VAT report** shows total taxable sales and VAT collected (VAT Payable balance).

## Quick start
1. Create venv, install requirements:
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```
2. Initialize DB and seed sample data:
```bash
python init_db.py
```
3. Run app:
```bash
python app.py
```
4. Visit `http://127.0.0.1:5000`

## Notes & Limitations
- This is a simplified accounting implementation for demo/MVP purposes only.
- For production or tax filing use, consult an accountant and add extensive validation, audits, permissions, and persistence best practices.
- VAT is fixed at 12% for this MVP but can be extended.

