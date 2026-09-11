from pool import nfl_teams


def test_all_32_teams_present():
    assert len(nfl_teams.FULL_TO_SHORT) == 32
    assert len(nfl_teams.CODE_TO_SHORT) == 32


def test_multi_word_city_short_names_disambiguated():
    assert nfl_teams.FULL_TO_SHORT["New York Jets"] == "NY Jets"
    assert nfl_teams.FULL_TO_SHORT["New York Giants"] == "NY Giants"
    assert nfl_teams.FULL_TO_SHORT["Los Angeles Chargers"] == "LA Chargers"
    assert nfl_teams.FULL_TO_SHORT["Los Angeles Rams"] == "LA Rams"


def test_code_lookup_matches_full_name_lookup():
    assert nfl_teams.CODE_TO_SHORT["ARI"] == nfl_teams.FULL_TO_SHORT["Arizona Cardinals"]
    assert nfl_teams.CODE_TO_SHORT["SF"] == nfl_teams.FULL_TO_SHORT["San Francisco 49ers"]
    assert nfl_teams.CODE_TO_SHORT["GB"] == nfl_teams.FULL_TO_SHORT["Green Bay Packers"]
