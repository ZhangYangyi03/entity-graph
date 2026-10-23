from entitygraph.holders import parse_cover

# Verbatim excerpt of the cover-page layout from Apple's SC 13G/A (accession
# 0001193125-24-036431). Kept short on purpose: it is a layout pin, not a fixture dump.
COVER = """
SCHEDULE 13G CUSIP No. 037833100 13G Page 5 of 48 Pages
1 NAME OF REPORTING PERSON Berkshire Hathaway Inc.
2 CHECK THE APPROPRIATE BOX IF A MEMBER OF A GROUP (a) [X] (b) []
3 SEC USE ONLY
4 CITIZENSHIP OR PLACE OF ORGANIZATION State of Delaware
NUMBER OF SHARES BENEFICIALLY OWNED BY EACH REPORTING PERSON
9 AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON 61,542,988 shares of Common Stock
10 CHECK BOX IF THE AGGREGATE AMOUNT IN ROW (9) EXCLUDES CERTAIN SHARES []
11 PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW 9 0.4%
12 TYPE OF REPORTING PERSON HC, CO
1 NAME OF REPORTING PERSON Government Employees Insurance Company
2 CHECK THE APPROPRIATE BOX IF A MEMBER OF A GROUP (a) [X] (b) []
9 AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON 56,350,988 shares of Common Stock
11 PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW 9 0.3%
"""


def test_two_holders_extracted_with_numbers():
    rows = parse_cover(COVER)
    names = [r["name"] for r in rows]
    assert "Berkshire Hathaway Inc" in names[0] or names[0].startswith("Berkshire")
    assert len(rows) == 2
    assert rows[0]["shares"] == 61542988
    assert rows[0]["percent"] == 0.4
    assert rows[1]["shares"] == 56350988


def test_boilerplate_is_not_a_holder():
    rows = parse_cover("1 NAME OF REPORTING PERSON See the cover pages 2 CHECK [X]")
    assert rows == []


def test_html_entities_are_unwrapped():
    html = "<td>1 NAME OF REPORTING PERSON</td><td>Vanguard&#160;Group</td><td>2 CHECK</td>"
    rows = parse_cover(html)
    assert rows and rows[0]["name"].startswith("Vanguard")
