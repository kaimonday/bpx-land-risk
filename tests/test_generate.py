import duckdb

from landrisk.db import RAW_DDL
from landrisk.generate_leases import _rrc_leases


def test_rrc_leases_come_back_in_a_stable_order():
    # The generator shuffles this list with a fixed seed, so its input order
    # must not depend on how DuckDB happens to schedule the group-by.
    con = duckdb.connect()
    con.execute(RAW_DDL)
    keys = [f"{n:05d}" for n in range(300)]
    rows = [("O", "08", k, f"2026{m:02d}", "1", "OP", "L", "F", "100", "0", "0", "0")
            for k in reversed(keys) for m in (1, 2)]
    con.executemany("insert into raw.rrc_lease_cycle values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())", rows)
    con.execute("set threads = 8")
    for _ in range(5):
        assert [r["key"][2] for r in _rrc_leases(con)] == keys
