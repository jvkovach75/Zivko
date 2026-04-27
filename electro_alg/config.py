from __future__ import annotations

FINAL_PROJECT_LAYER_PATTERNS = [
    "text zivko",
    "s_lider",
    "novi kabl",
    "struja",
    "ee tekst",
    "ee kote",
    "ee razvodni",
    "ee 150",
    "schreder",
    "el svetiljke",
    "tcg_cijev",
    "tcg_cijev_oznaka",
    "tcg_trasa",
    "10kv puhovo",
    "10kv ka krstac kula",
    "10kv fapromal",
    "10kv ka lisice 3",
    "0 predlog 10kv",
]

EXISTING_NETWORK_LAYER_PATTERNS = [
    "t2_elektroene",
    "l2_elektroene",
    "elektrovodovi",
    "elektromree",
    "elektromrez",
    "mreza",
    "10kv",
]

CONDITION_LAYER_PATTERNS = [
    "telekom",
    "vodovod",
    "optika",
    "cijev",
]

BASE_LAYER_PATTERNS = [
    "parcela",
    "granica",
    "ko granica",
    "put",
    "kolovoz",
    "trotoar",
    "bankina",
    "stac",
    "stations",
    "osa",
    "visinske",
    "ograde",
    "vegetacija",
    "most",
    "kanal",
    "saht",
    "geodezija",
    "konstrukcija",
    "sipovi",
    "hidroizolacija",
    "zgrade",
    "naziv",
    "skarpa",
    "zid",
]

PROJECT_LAYER_PATTERNS = FINAL_PROJECT_LAYER_PATTERNS

CORRIDOR_FALLBACK_LAYER_PATTERNS = [
    "elektroene",
    "mreza",
    "put",
    "kolovoz",
    "trotoar",
    "bankina",
    "most",
    "osa",
    "rehab",
    "ivice",
    "ivica",
    "stac",
    "stations",
    "kanal",
    "telekom",
    "vodovod",
    "tcg_trasa",
]

PREFERRED_CORRIDOR_LAYER_PATTERNS = [
    "elektroene",
    "mreza",
    "tcg_trasa",
]

ROAD_CORRIDOR_LAYER_PATTERNS = [
    "put",
    "kolovoz",
    "trotoar",
    "bankina",
    "osa",
    "rehab",
    "ivice",
    "ivica",
    "stac",
    "stations",
]

ALLOWED_CORRIDOR_LAYER_PATTERNS = [
    "put",
    "kolovoz",
    "trotoar",
    "bankina",
    "osa",
    "rehab",
    "ivice",
    "ivica",
    "stac",
    "stations",
    "telekom",
    "vodovod",
    "cijev",
    "kanal",
]

CONDITIONAL_CORRIDOR_LAYER_PATTERNS = [
    "most",
    "pesacka staza mosta",
]

CORRIDOR_CLASS_WEIGHTS = {
    "preferred": 1.0,
    "allowed": 1.9,
    "road": 1.1,
    "conditional": 2.5,
}

PROPOSED_ELECTRICAL_LAYERS = [
    "EL_ROUTE",
    "EL_ROUTE_LABEL",
    "EL_STRUCTURE",
    "EL_NOTE",
    "Text Zivko",
    "s_LIDER",
    "10kV Puhovo",
    "10kV ka Krstac Kula",
    "10kV Fapromal",
    "10kV ka Lisice 3",
    "L2_Elektroene",
]

PROJECT_TEXT_LAYER = "Text Zivko"
PROJECT_LEADER_LAYER = "s_LIDER"

KRSTAC_TEXT_LAYER_ATTRS = {
    "color": 7,
    "linetype": "Continuous",
    "lineweight": -3,
}

KRSTAC_LEADER_LAYER_ATTRS = {
    "color": 7,
    "linetype": "Continuous",
    "lineweight": 18,
}

KRSTAC_TEXT_ENTITY_ATTRS = {
    "color": 4,
    "linetype": "BYLAYER",
    "lineweight": -1,
    "char_height": 6.0,
    "style": "VIP",
    "attachment_point": 7,
}

KRSTAC_ROUTE_ENTITY_ATTRS_BY_LAYER = {
    "10kV Puhovo": {"color": 1, "linetype": "BYLAYER", "lineweight": 40},
    "10kV ka Krstac Kula": {"color": 6, "linetype": "BYLAYER", "lineweight": 40},
    "10kV Fapromal": {"color": 256, "linetype": "BYLAYER", "lineweight": -1},
    "10kV ka Lisice 3": {"color": 30, "linetype": "BYLAYER", "lineweight": 40},
    "L2_Elektroene": {"color": 256, "linetype": "BYLAYER", "lineweight": -1},
    "T2_Elektroene": {"color": 256, "linetype": "BYLAYER", "lineweight": -1},
}

PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE = {
    "1A": "10kV ka Krstac Kula",
    "1B": "10kV Puhovo",
    "2": "10kV ka Krstac Kula",
    "3": "10kV Fapromal",
    "4": "10kV ka Lisice 3",
    "5": "L2_Elektroene",
}

OUTPUT_GUIDE_LAYERS = {
    1: ["10kV Puhovo", "10kV"],
    2: ["10kV ka Krstac Kula", "10kV"],
    3: ["10kV Fapromal", "10kV"],
    4: ["10kV ka Lisice 3", "10kV"],
    5: ["L2_Elektroene", "10kV ka Lisice 3", "10kV"],
}

OUTPUT_GUIDE_LAYERS_BY_CODE = {
    "1A": ["10kV ka Krstac Kula", "10kV Puhovo", "10kV"],
    "1B": ["10kV Puhovo", "10kV"],
}

FINAL_LEARNED_ROUTE_PRIORS_BY_CODE = {
    "1A": {
        "preferred_layers": ["10kV ka Krstac Kula", "10kV", "T2_Elektroene"],
        "max_terminal_jump": 180.0,
    },
    "1B": {
        "preferred_layers": ["10kV Puhovo", "10kV", "T2_Elektroene"],
        "max_terminal_jump": 180.0,
    },
    "2": {
        "preferred_layers": ["10kV ka Krstac Kula", "10kV", "L2_Elektroene"],
        "max_terminal_jump": 180.0,
    },
    "3": {
        "preferred_layers": ["10kV Fapromal", "10kV", "L2_Elektroene"],
        "max_terminal_jump": 180.0,
    },
    "4": {
        "preferred_layers": ["10kV ka Lisice 3", "10kV", "L2_Elektroene"],
        "max_terminal_jump": 180.0,
    },
    "5": {
        "preferred_layers": ["L2_Elektroene", "10kV ka Lisice 3", "10kV"],
        "max_terminal_jump": 180.0,
    },
}

EXPECTED_ROUTE_LENGTHS = {
    1: (200.0, 320.0),
    2: (900.0, 1250.0),
    3: (550.0, 800.0),
    4: (450.0, 650.0),
    5: (280.0, 420.0),
}

EXPECTED_ROUTE_LENGTHS_BY_CODE = {
    "1A": (110.0, 160.0),
    "1B": (240.0, 280.0),
}

UNDERGROUND_CABLE_PHASE_FACTOR = 3.0
UNDERGROUND_CABLE_ROUTE_RESERVE_FACTOR = 1.036
UNDERGROUND_CABLE_TERMINAL_ALLOWANCE_PER_PHASE = 25.0

OVERHEAD_CONDUCTOR_PHASE_FACTOR = 3.0
OVERHEAD_CONDUCTOR_ROUTE_RESERVE_FACTOR = 1.0
OVERHEAD_CONDUCTOR_TERMINAL_ALLOWANCE_PER_PHASE = 9.0

ANCHOR_KEYWORDS = {
    "ts 35/10kv krstac": ["ts 35/10kv krstac", "krstac"],
    "uzb stub broj 1": ["uzb stub broj 1", "uz stub broj 1", "novi stub br. 1", "novi uz stub broj 1"],
    "krstac-kula": ["krstac-kula", "krstac - kula"],
    "fapromal": ["fapromal"],
    "lisice 3": ["lisice 3"],
    "lisice 1": ["lisice 1"],
    "puhovo - krstac": ["puhovo - krstac", "puhovo"],
    "k03": ["k03"],
    "k04": ["k04"],
    "k05": ["k05"],
    "k06": ["k06"],
}

ANCHOR_CONTEXT = {
    "ts 35/10kv krstac": {
        "required_any": ["ts", "trafostanica"],
        "required_all": ["krstac"],
        "preferred": ["35/10kv", "krstac"],
        "excluded": ["ko krstac"],
    },
    "uzb stub broj 1": {
        "required_any": ["stub broj 1", "stub br 1", "uz stub broj 1", "uzb stub broj 1"],
        "preferred": ["novi", "12m", "1600", "prelaz", "podzemnog", "nadzemni"],
        "excluded": [],
    },
    "krstac-kula": {
        "required_all": ["krstac", "kula"],
        "preferred": ["ts", "10/0,4kv"],
        "excluded": [],
    },
    "fapromal": {
        "required_any": ["fapromal"],
        "preferred": ["mbts", "sahte", "sahta", "10/0,4kv"],
        "excluded": [],
    },
    "lisice 3": {
        "required_any": ["lisice 3"],
        "preferred": ["pts", "ts", "10/0,4kv"],
        "excluded": [],
    },
    "lisice 1": {
        "required_all": ["lisice 1"],
        "preferred": ["ts", "10/0,4kv"],
        "excluded": [],
    },
    "puhovo - krstac": {
        "required_all": ["puhovo", "krstac"],
        "preferred": ["dv", "10kv", "uzb"],
        "excluded": [],
    },
    "k03": {"required_any": ["k03"], "preferred": ["izvodna", "celija", "10kv"], "excluded": []},
    "k04": {"required_any": ["k04"], "preferred": ["izvodna", "celija", "10kv"], "excluded": []},
    "k05": {"required_any": ["k05"], "preferred": ["izvodna", "celija", "10kv"], "excluded": []},
    "k06": {"required_any": ["k06"], "preferred": ["izvodna", "celija", "10kv"], "excluded": []},
}
