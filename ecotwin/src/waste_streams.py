"""
Waste stream configuration, campus segregation policies, reward logic,
and simulated environmental emission factors.
"""

# Configurable Waste Streams available on campus
# A 3-stream campus architecture is typical for educational institutions:
# 1. 'dry_recyclable' : Paper, cardboard, plastics
# 2. 'glass_metal'    : Glass bottles, aluminium and steel cans
# 3. 'residual'       : Contaminated items, non-recyclable general trash
CAMPUS_STREAMS = {
    "dry_recyclable": {
        "name": "Dry Recyclables",
        "color": "#3B82F6",  # Blue
        "description": "Clean, dry materials suitable for mechanical recycling (paper, cardboard, plastics).",
    },
    "glass_metal": {
        "name": "Glass & Metals",
        "color": "#10B981",  # Green
        "description": "Inorganic containers (glass jars/bottles, beverage cans, tins).",
    },
    "residual": {
        "name": "Residual Waste (General Trash)",
        "color": "#6B7280",  # Gray
        "description": "Non-recyclable or soiled items destined for municipal treatment/sanitary landfill.",
    },
}

# Mapping 6 AI Vision Classes -> Campus Waste Streams
# Also defines:
# - eligible_for_rewards (bool)
# - ecocoin_reward (int)
# - estimated_weight_kg_per_item (float)
# - co2_factor_kg_per_kg (documented EPA WARM / UK DEFRA emission factor approximation)
CLASS_TO_STREAM_MAPPING = {
    "cardboard": {
        "stream_id": "dry_recyclable",
        "is_recyclable": True,
        "eligible_for_rewards": True,
        "ecocoin_reward": 10,
        "est_weight_kg": 0.15,
        "co2e_factor_kg_per_kg": 0.95,  # ~0.95 kg CO2e avoided per kg recycled cardboard
        "rationale": "High-value pulp fibers; saves trees and reduces landfill methane.",
    },
    "paper": {
        "stream_id": "dry_recyclable",
        "is_recyclable": True,
        "eligible_for_rewards": True,
        "ecocoin_reward": 10,
        "est_weight_kg": 0.05,
        "co2e_factor_kg_per_kg": 1.10,  # ~1.10 kg CO2e avoided per kg clean paper
        "rationale": "Readily repulpable; saves water, bleaching chemicals, and energy.",
    },
    "plastic": {
        "stream_id": "dry_recyclable",
        "is_recyclable": True,
        "eligible_for_rewards": True,
        "ecocoin_reward": 15,
        "est_weight_kg": 0.04,
        "co2e_factor_kg_per_kg": 1.45,  # ~1.45 kg CO2e avoided per kg virgin plastic displaced
        "rationale": "Displaces fossil petrochemical feedstock; prevents marine microplastic pollution.",
    },
    "metal": {
        "stream_id": "glass_metal",
        "is_recyclable": True,
        "eligible_for_rewards": True,
        "ecocoin_reward": 15,
        "est_weight_kg": 0.08,
        "co2e_factor_kg_per_kg": 2.20,  # ~2.20 kg CO2e avoided per kg recycled aluminium/steel
        "rationale": "Remelting aluminium uses 95% less energy than primary bauxite smelting.",
    },
    "glass": {
        "stream_id": "glass_metal",
        "is_recyclable": True,
        "eligible_for_rewards": True,
        "ecocoin_reward": 10,
        "est_weight_kg": 0.25,
        "co2e_factor_kg_per_kg": 0.31,  # ~0.31 kg CO2e avoided per kg recycled cullet
        "rationale": "100% recyclable infinitely without degradation of purity or strength.",
    },
    "trash": {
        "stream_id": "residual",
        "is_recyclable": False,
        "eligible_for_rewards": False,
        "ecocoin_reward": 0,
        "est_weight_kg": 0.10,
        "co2e_factor_kg_per_kg": 0.0,
        "rationale": "Residual general waste; not eligible for circular recycling credits or EcoCoins.",
    },
}

# Campus Virtual Bins Layout
CAMPUS_LOCATIONS = [
    {"id": "LIB", "name": "Central Library"},
    {"id": "CAF", "name": "Food Court / Cafeteria"},
    {"id": "ENG", "name": "Engineering Quad"},
    {"id": "STU", "name": "Student Activities Center"},
]

def calculate_co2_saving(waste_class: str) -> float:
    """
    Calculates estimated CO2e avoided (in kg) for one disposed item.
    NOTE: Simulated estimate based on published emission factor benchmarks.
    """
    mapping = CLASS_TO_STREAM_MAPPING.get(waste_class)
    if not mapping or not mapping["eligible_for_rewards"]:
        return 0.0
    return round(mapping["est_weight_kg"] * mapping["co2e_factor_kg_per_kg"], 4)

def get_stream_for_class(waste_class: str) -> str:
    """Returns stream_id for a given AI waste class."""
    return CLASS_TO_STREAM_MAPPING.get(waste_class, {}).get("stream_id", "residual")
