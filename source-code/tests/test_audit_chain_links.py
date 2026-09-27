"""P09.05/P09.10: the chain verifier resolves a link to any row of the table, so concurrent writers do not read as tampering."""

from backend import audit_chain


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return Cursor(self.rows)


def verify(rows, deletable=False):
    return audit_chain.verify_table(Connection(rows), "t", "id", deletable)


GENESIS = "genesis:t"


def test_a_plain_chain_is_intact():
    assert verify([(1, GENESIS, "h1", True), (2, "h1", "h2", True), (3, "h2", "h3", True)])[
        "intact"
    ]


def test_two_writers_that_committed_in_the_opposite_order_of_their_ids_are_not_tampering():
    rows = [
        (1, GENESIS, "h1", True),
        (2, "h3", "h2", True),
        (3, "h1", "h3", True),
    ]  # id 2 linked to id 3
    result = verify(rows)
    assert result["intact"]
    assert result["broken_links"] == []


def test_a_fork_two_rows_linking_to_the_same_predecessor_is_not_tampering():
    assert verify([(1, GENESIS, "h1", True), (2, "h1", "h2", True), (3, "h1", "h3", True)])[
        "intact"
    ]


def test_a_link_to_a_row_that_exists_nowhere_is_broken():
    result = verify([(1, GENESIS, "h1", True), (2, "gone", "h2", True)])
    assert result["broken_links"] == [2]
    assert not result["intact"]


def test_a_link_to_a_row_that_no_longer_exists_is_a_gap_where_deletion_is_sanctioned_and_broken_where_it_is_not():
    rows = [(1, GENESIS, "h1", True), (2, "purged", "h2", True)]
    assert verify(rows, deletable=True)["gaps"] == [2]
    assert verify(rows, deletable=True)["intact"]
    assert not verify(rows, deletable=False)["intact"]


def test_a_rewritten_row_is_caught_by_its_hash():
    result = verify([(1, GENESIS, "h1", True), (2, "h1", "h2", False)])
    assert result["hash_mismatches"] == [2]
    assert not result["intact"]
