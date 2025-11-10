"""
Universal SKU Auto-Generation System
Works for ANY retail business type
"""
from models import db, Product
import re


# ✅ UNIVERSAL CATEGORY PRESETS (Expandable)
INDUSTRY_CATEGORIES = {
    # Automotive
    'automotive': {
        'TIR': 'Tires',
        'FIL': 'Filters',
        'BRK': 'Brakes',
        'BAT': 'Battery',
        'OIL': 'Oil/Lubricants',
        'SPK': 'Spark Plugs',
        'WIP': 'Wipers',
        'MIR': 'Mirrors',
        'LGT': 'Lights',
        'CAB': 'Cables',
        'BLT': 'Belts',
    },
    
    # Construction/Hardware
    'construction': {
        'CEM': 'Cement',
        'SND': 'Sand',
        'GRV': 'Gravel',
        'PLY': 'Plywood',
        'PNT': 'Paint',
        'NAL': 'Nails/Screws',
        'WIR': 'Wire/Cable',
        'TUB': 'Pipes/Tubes',
        'TOL': 'Tools',
        'ELC': 'Electrical',
    },
    
    # Apparel/Boutique
    'apparel': {
        'DRS': 'Dresses',
        'TOP': 'Tops/Blouses',
        'PNT': 'Pants/Jeans',
        'SKT': 'Skirts',
        'SHO': 'Shoes',
        'BAG': 'Bags',
        'ACC': 'Accessories',
        'UND': 'Underwear',
        'SWT': 'Sweaters',
        'OUT': 'Outerwear',
    },
    
    # Beauty & Skincare
    'beauty': {
        'SKN': 'Skincare',
        'MKP': 'Makeup',
        'FRG': 'Fragrance',
        'HRC': 'Haircare',
        'BDY': 'Body Care',
        'TON': 'Toner',
        'SRM': 'Serum',
        'MST': 'Moisturizer',
        'CLN': 'Cleanser',
        'MSK': 'Mask',
    },
    
    # Food & Beverage
    'foodbev': {
        'MLK': 'Milk Tea',
        'COF': 'Coffee',
        'JCE': 'Juice',
        'SNK': 'Snacks',
        'SIN': 'Sinkers/Add-ons',
        'CUP': 'Cups',
        'SYR': 'Syrup',
        'PWD': 'Powder',
        'ICE': 'Ice/Frozen',
        'PCK': 'Packaging',
    },
    
    # General/Universal (Default)
    'general': {
        'PRD': 'Product',
        'ITM': 'Item',
        'GDS': 'Goods',
        'MRC': 'Merchandise',
        'SUP': 'Supplies',
    }
}


def generate_sku(product_name, category=None, custom_sku=None, industry=None):
    """
    Universal SKU generator for any retail business.
    
    Args:
        product_name: Name of the product
        category: Optional category code (e.g., "TIR", "DRS", "SKN")
        custom_sku: Optional manual SKU (validated for uniqueness)
        industry: Optional industry hint for auto-detection
    
    Returns:
        Unique SKU string
    
    Examples:
        generate_sku("Leo Tires", "TIR") -> "TIR-00001"
        generate_sku("Floral Dress", "DRS") -> "DRS-00001"
        generate_sku("Milk Tea Large") -> "PRD-00001" (generic)
        generate_sku("Custom Product", custom_sku="CUSTOM-001") -> "CUSTOM-001"
    """
    
    # 1. CUSTOM SKU: Validate and use if provided
    if custom_sku and custom_sku.strip():
        custom_sku = custom_sku.strip().upper()
        
        # Validate format
        if not re.match(r'^[A-Z0-9-]+$', custom_sku):
            raise ValueError("SKU can only contain letters, numbers, and hyphens")
        
        if len(custom_sku) > 64:
            raise ValueError("SKU is too long (max 64 characters)")
        
        # Check uniqueness
        existing = Product.query.filter_by(sku=custom_sku).first()
        if existing:
            raise ValueError(f"SKU '{custom_sku}' already exists for: {existing.name}")
        
        return custom_sku
    
    # 2. DETERMINE PREFIX
    if category and category.strip():
        # User provided category code
        prefix = category.strip().upper()[:3]
    else:
        # Auto-detect from product name
        prefix = auto_detect_category(product_name, industry)
    
    # 3. GENERATE SEQUENTIAL NUMBER
    # Find last SKU with this prefix
    last_product = Product.query.filter(
        Product.sku.like(f'{prefix}-%')
    ).order_by(Product.sku.desc()).first()
    
    if last_product:
        try:
            # Extract number from SKU (e.g., "TIR-00005" -> 5)
            parts = last_product.sku.split('-')
            last_num_str = parts[-1]
            last_num = int(last_num_str)
            next_num = last_num + 1
        except (ValueError, IndexError):
            next_num = 1
    else:
        next_num = 1
    
    # 4. FORMAT SKU
    new_sku = f"{prefix}-{next_num:05d}"
    
    # 5. FINAL UNIQUENESS CHECK (rare edge case)
    if Product.query.filter_by(sku=new_sku).first():
        # Collision detected, add timestamp suffix
        from datetime import datetime
        timestamp = datetime.now().strftime('%H%M%S')
        new_sku = f"{prefix}-{next_num:05d}-{timestamp}"
    
    return new_sku


def auto_detect_category(product_name, industry=None):
    """
    Smart category detection from product name.
    
    Args:
        product_name: Product name to analyze
        industry: Optional industry hint
    
    Returns:
        3-letter category prefix
    """
    name_lower = product_name.lower()
    
    # Define keyword mappings for auto-detection
    keywords = {
        # Automotive
        'TIR': ['tire', 'tires', 'gulong'],
        'FIL': ['filter', 'air filter', 'oil filter'],
        'BRK': ['brake', 'brakes', 'preno'],
        'OIL': ['oil', 'lubricant', 'langis'],
        'BAT': ['battery', 'baterya'],
        'SPK': ['spark plug', 'spark'],
        
        # Construction
        'CEM': ['cement', 'semento'],
        'SND': ['sand', 'buhangin'],
        'PLY': ['plywood', 'wood'],
        'PNT': ['paint', 'pintura'],
        
        # Apparel
        'DRS': ['dress', 'damit'],
        'TOP': ['top', 'blouse', 'shirt'],
        'PNT': ['pants', 'jeans', 'slacks'],
        'SHO': ['shoes', 'sapatos'],
        'BAG': ['bag', 'purse'],
        
        # Beauty
        'SKN': ['skin', 'skincare', 'face'],
        'MKP': ['makeup', 'lipstick', 'foundation'],
        'CLN': ['cleanser', 'wash'],
        'TON': ['toner'],
        'SRM': ['serum'],
        
        # Food & Beverage
        'MLK': ['milk tea', 'milktea'],
        'COF': ['coffee', 'kape'],
        'JCE': ['juice'],
        'SNK': ['snack'],
    }
    
    # Try to match keywords
    for prefix, keywords_list in keywords.items():
        for keyword in keywords_list:
            if keyword in name_lower:
                return prefix
    
    # No match found: Use generic prefix
    return 'PRD'


def get_industry_categories(industry='general'):
    """
    Get category presets for a specific industry.
    
    Args:
        industry: Industry type ('automotive', 'construction', 'apparel', etc.)
    
    Returns:
        dict: Category code -> name mapping
    """
    return INDUSTRY_CATEGORIES.get(industry, INDUSTRY_CATEGORIES['general'])


def get_all_categories():
    """
    Get all available category presets across all industries.
    
    Returns:
        dict: Combined categories from all industries
    """
    combined = {}
    for industry_cats in INDUSTRY_CATEGORIES.values():
        combined.update(industry_cats)
    return combined


def get_category_suggestions():
    """
    Get suggested category prefixes for the bulk upload template.
    Returns a flattened list of all categories across industries.
    
    Returns:
        dict: Category prefix -> description mapping
    """
    suggestions = {}
    
    # Combine all industry categories into one dictionary
    for industry_name, categories in INDUSTRY_CATEGORIES.items():
        for prefix, description in categories.items():
            # Add industry hint to description
            if industry_name != 'general':
                suggestions[prefix] = f"{description} ({industry_name.title()})"
            else:
                suggestions[prefix] = description
    
    return dict(sorted(suggestions.items()))


def validate_sku(sku):
    """
    Validate SKU format and uniqueness.
    
    Args:
        sku: SKU string to validate
    
    Returns:
        tuple: (is_valid, error_message)
    """
    if not sku or not sku.strip():
        return False, "SKU cannot be empty"
    
    sku = sku.strip().upper()
    
    if len(sku) > 64:
        return False, "SKU is too long (max 64 characters)"
    
    if not re.match(r'^[A-Z0-9-]+$', sku):
        return False, "SKU can only contain letters, numbers, and hyphens"
    
    existing = Product.query.filter_by(sku=sku).first()
    if existing:
        return False, f"SKU already exists for: {existing.name}"
    
    return True, None


def suggest_sku(product_name, industry=None):
    """
    Suggest multiple SKU options for a product.
    
    Args:
        product_name: Product name
        industry: Optional industry hint
    
    Returns:
        list: List of suggested SKUs
    """
    suggestions = []
    
    # Option 1: Auto-detected category
    auto_prefix = auto_detect_category(product_name, industry)
    suggestions.append({
        'sku': generate_sku(product_name, category=auto_prefix),
        'description': f'Auto-detected ({auto_prefix})'
    })
    
    # Option 2: Generic
    if auto_prefix != 'PRD':
        suggestions.append({
            'sku': generate_sku(product_name, category='PRD'),
            'description': 'Generic product code'
        })
    
    # Option 3: From product name initials
    words = re.sub(r'[^A-Za-z0-9\s]', '', product_name).split()
    if len(words) >= 2:
        initials = ''.join(word[0] for word in words[:3]).upper()
        suggestions.append({
            'sku': generate_sku(product_name, category=initials),
            'description': f'Name-based ({initials})'
        })
    
    return suggestions