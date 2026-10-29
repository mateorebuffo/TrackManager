"""Casos reales de logs de oct 2026: lo que Muzpa marcaba satisfies=True."""
from download.muzpa import _matches


def _t(artist, title, subtitle=None):
    return {"artist": artist, "title": title, "subtitle": subtitle}


def test_accepts_the_right_track():
    assert _matches("Jamback - Can't Resist", _t("Jamback", "Can't Resist"))
    assert _matches("ADR (UK) - RIDE", _t("ADR (UK), OUTTEN", "RIDE"))
    assert _matches("DJ CARPET - Torino", _t("vano1337, DJ CARPET", "Torino"))
    assert _matches("Subb-an - System Dub", _t("Subb-an", "System", "Dub Mix"))
    assert _matches("Emi Ömar - Zenro", _t("B1 Emi Omar", "Zenro VINYL ONLY "))
    assert _matches("Ki.Mi. & doradice - Mirage", _t("Ki.Mi. & doradice.", "B1 Mirage VINYL ONLY "))
    assert _matches("Traumer, Andrew Azara - Need You", _t("Traumer, Andrew Azara", "Need You"))
    assert _matches("NAAiV - Hearthside (Betsu Afterhours Mix)", _t("NAAiV", "Hearthside"))


def test_rejects_what_muzpa_called_satisfying():
    assert not _matches("A to B - Merging", _t("Colyn", "Merging Realities", "Original Mix"))
    assert not _matches("A to B - Frankie's Black", _t("Betty Black & Sophia", "Frankie Goes To The Airport"))
    assert not _matches("Wink - Dancing", _t("Winkler", "Dancing Forever"))
    assert not _matches("Dece - No End", _t("Boo Williams", "Return of No End"))
    assert not _matches("Lee Burton - Music", _t("Lee Burton", "VOT", "Original Mix"))
    assert not _matches("Unknown Artist - Kcik 20", _t("Unknown Artist (Kcik), Marii Lim", "Kcik 40 (feat. Marii Lim)"))


def test_query_without_separator_is_not_filtered():
    assert _matches("Traumer District", _t("Someone", "Else"))
