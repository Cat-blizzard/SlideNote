from slidenote.models import Deck, SlidePage, TableBlock
from slidenote.table_understanding import enrich_deck_with_table_understanding, table_preview


def test_local_table_understanding_adds_summary_conclusion_and_key_rows():
    table = TableBlock(
        id="s1_tbl1",
        rows=[
            ["Protocol", "Reliability", "Cost"],
            ["TCP", "Reliable ordered delivery", "Higher overhead"],
            ["UDP", "Best effort delivery", "Lower overhead"],
        ],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, tables=[table])])

    report = enrich_deck_with_table_understanding(deck)

    assert report["summary"]["tables_total"] == 1
    assert report["summary"]["tables_with_summary"] == 1
    assert report["summary"]["tables_with_conclusion"] == 1
    assert table.table_summary is not None
    assert "Protocol" in table.table_summary
    assert table.table_conclusion is not None
    assert "TCP" in table.table_conclusion
    assert "UDP" in table.table_conclusion
    assert [row["label"] for row in table.key_rows] == ["TCP", "UDP"]
    assert table.key_rows[0]["values"][1] == {"column": "Reliability", "value": "Reliable ordered delivery"}
    assert "关键行" in table_preview(table, limit=500)


def test_local_table_understanding_handles_empty_tables():
    table = TableBlock(id="s2_tbl1", rows=[["", ""], []])
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=2, tables=[table])])

    report = enrich_deck_with_table_understanding(deck)

    assert report["summary"]["tables_total"] == 1
    assert table.table_summary is None
    assert table.table_conclusion is None
    assert table.key_rows == []
    assert report["pages"][0]["tables"][0]["warnings"] == ["empty_table"]
