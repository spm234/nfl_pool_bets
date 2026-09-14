"""NFL team name mapping: full mascot name <-> 3-letter code <-> the short
city name this project's own data already uses (from the pool's own export
format, e.g. "NY Jets" not "New York Jets", "LA Chargers" not "Los Angeles
Chargers"). Needed to reconcile external sources (lookahead spread/
moneyline sheets, keyed by full name + 3-letter opponent codes) against
games already in the DB (keyed by the pool's short names) without creating
duplicate game rows for the same real game under a different spelling.
"""
from __future__ import annotations

# (full mascot name, 3-letter/code, short name used throughout this project)
_TEAMS = [
    ("Arizona Cardinals", "ARI", "Arizona"),
    ("Atlanta Falcons", "ATL", "Atlanta"),
    ("Baltimore Ravens", "BAL", "Baltimore"),
    ("Buffalo Bills", "BUF", "Buffalo"),
    ("Carolina Panthers", "CAR", "Carolina"),
    ("Chicago Bears", "CHI", "Chicago"),
    ("Cincinnati Bengals", "CIN", "Cincinnati"),
    ("Cleveland Browns", "CLE", "Cleveland"),
    ("Dallas Cowboys", "DAL", "Dallas"),
    ("Denver Broncos", "DEN", "Denver"),
    ("Detroit Lions", "DET", "Detroit"),
    ("Green Bay Packers", "GB", "Green Bay"),
    ("Houston Texans", "HOU", "Houston"),
    ("Indianapolis Colts", "IND", "Indianapolis"),
    ("Jacksonville Jaguars", "JAX", "Jacksonville"),
    ("Kansas City Chiefs", "KC", "Kansas City"),
    ("Los Angeles Chargers", "LAC", "LA Chargers"),
    ("Los Angeles Rams", "LAR", "LA Rams"),
    ("Las Vegas Raiders", "LV", "Las Vegas"),
    ("Miami Dolphins", "MIA", "Miami"),
    ("Minnesota Vikings", "MIN", "Minnesota"),
    ("New England Patriots", "NE", "New England"),
    ("New Orleans Saints", "NO", "New Orleans"),
    ("New York Giants", "NYG", "NY Giants"),
    ("New York Jets", "NYJ", "NY Jets"),
    ("Philadelphia Eagles", "PHI", "Philadelphia"),
    ("Pittsburgh Steelers", "PIT", "Pittsburgh"),
    ("Seattle Seahawks", "SEA", "Seattle"),
    ("San Francisco 49ers", "SF", "San Francisco"),
    ("Tampa Bay Buccaneers", "TB", "Tampa Bay"),
    ("Tennessee Titans", "TEN", "Tennessee"),
    ("Washington Commanders", "WAS", "Washington"),
]

CODE_TO_SHORT = {code: short for _, code, short in _TEAMS}
FULL_TO_SHORT = {full: short for full, _, short in _TEAMS}
