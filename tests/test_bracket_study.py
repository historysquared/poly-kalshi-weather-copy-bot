from weather_alpha.settlement.bracket_study import boundary_distance_f, contract_contains, evaluate_contract_flip


def test_contract_contains_bucket_and_tails():
    assert contract_contains("bucket", 86, 87, 86) is True
    assert contract_contains("bucket", 86, 87, 88) is False
    assert contract_contains("above", 93, None, 94) is True
    assert contract_contains("below", None, 85, 86) is False


def test_boundary_flip_when_cli_and_asos_land_in_different_contract_outcomes():
    contract = {"contract_id": "X", "shape": "bucket", "lower": 86.0, "upper": 87.0}
    flip = evaluate_contract_flip(contract, official_f=87.0, public_f=88.0, reconstructed_f=88.0)
    assert flip.official_winner is True
    assert flip.public_winner is False
    assert flip.public_flip is True
    assert flip.official_boundary_distance_f == 0.0


def test_no_flip_when_difference_does_not_change_bucket():
    contract = {"contract_id": "X", "shape": "above", "lower": 90.0, "upper": None}
    flip = evaluate_contract_flip(contract, official_f=92.0, public_f=91.0, reconstructed_f=91.0)
    assert flip.official_winner is True
    assert flip.public_winner is True
    assert flip.public_flip is False
    assert boundary_distance_f("above", 90, None, 91) == 1.0
