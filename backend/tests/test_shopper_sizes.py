from app import shop


def test_loose_size_text_matches_the_catalogue_size():
    assert shop.match_size("queen bed", ["Single", "Queen", "King"]) == "Queen"
    assert shop.match_size("Queen", ["Single", "Queen", "King"]) == "Queen"
    assert shop.match_size("16 inch", ["16 in", "18 in"]) == "16 in"
    assert shop.match_size('18"', ["16 in", "18 in"]) == "18 in"


def test_no_match_and_no_sizes():
    assert shop.match_size("double", ["Single", "Queen", "King"]) is None
    assert shop.match_size("queen", []) is None
    assert shop.match_size(None, ["Single"]) is None
