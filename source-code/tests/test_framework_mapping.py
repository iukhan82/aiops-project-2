"""P09.07: the framework mapping's own rules (the verifier checks the same against the register and the documents)."""

import json
import re

from security import framework_mapping as fm
from security import verify_control_mapping as vcm

CONTROLS = {c["id"]: c for c in json.loads(vcm.CONTROLS.read_text(encoding="utf-8"))["controls"]}
DATA = fm.build()
ITEMS = [(f["id"], i) for f in DATA["frameworks"] for i in f["items"]]


def test_the_json_on_disk_is_what_the_source_produces():
    assert fm.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") == fm.render(DATA)


def test_iso_27001_annex_a_is_complete_and_unique():
    refs = [i["ref"] for f in DATA["frameworks"] if f["id"] == "iso27001" for i in f["items"]]
    assert sorted(refs) == sorted(vcm.ISO_EXPECTED)
    assert len(refs) == len(set(refs)) == 93


def test_every_cited_control_exists_and_every_control_is_cited():
    cited = {c for _, i in ITEMS for c in i["controls"]}
    assert cited <= set(CONTROLS)
    assert set(CONTROLS) <= cited


def test_coverage_is_computed_from_control_state_never_written():
    def item(controls, docs=(), applies="applies"):
        return {"applicability": applies, "controls": list(controls), "docs": list(docs)}

    implemented = [c for c, v in CONTROLS.items() if v["status"] == "implemented"][:2]
    not_done = [c for c, v in CONTROLS.items() if v["status"] != "implemented"][:1]
    assert vcm.coverage(item(implemented), CONTROLS) == "evidenced"
    assert vcm.coverage(item(implemented + not_done), CONTROLS) == "partial"
    assert vcm.coverage(item([]), CONTROLS) == "gap"
    assert vcm.coverage(item([], docs=["x.md"]), CONTROLS) == "documented"
    assert vcm.coverage(item(implemented, applies="not_applicable"), CONTROLS) == "out_of_scope"


def test_an_item_is_evidenced_only_if_every_control_it_cites_is_implemented():
    for _, i in ITEMS:
        if vcm.coverage(i, CONTROLS) == "evidenced":
            assert all(CONTROLS[c]["status"] == "implemented" for c in i["controls"])


def test_no_item_claims_compliance_and_the_statement_disclaims_it():
    for _, i in ITEMS:
        for field in ("note", "accepted"):
            assert not (i[field] and vcm.CLAIM_WORDS.search(i[field])), i["ref"]
    assert "no claim" in DATA["statement"] and "not legal advice" in DATA["statement"]


def test_owners_are_real_role_codes_and_not_applicable_items_say_why_and_cite_nothing():
    for _, i in ITEMS:
        assert i["owner"] in vcm.OWNERS
        assert i["applicability"] in vcm.APPLICABILITY
        if i["applicability"] == "not_applicable":
            assert len(i["note"]) >= 20 and not i["controls"], i["ref"]


def test_next_tasks_look_like_register_ids():
    for _, i in ITEMS:
        assert i["next"] is None or re.fullmatch(r"P\d\d\.\d\d", i["next"]), i["ref"]
