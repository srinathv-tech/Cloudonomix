import sqlite3

import pandas as pd

import config

# name -> (columns, primary key clause). Every table carries a `cloud` column
# ('azure' or 'aws'). Databases from the Azure-only version are migrated in place.
TABLES = {
    "costs": ([("date", "TEXT"), ("cloud", "TEXT"), ("service", "TEXT"),
               ("resource_group", "TEXT"), ("cost", "REAL"), ("currency", "TEXT")],
              "PRIMARY KEY(date, cloud, service, resource_group)"),
    "resource_costs": ([("date", "TEXT"), ("cloud", "TEXT"), ("resource_id", "TEXT"),
                        ("cost", "REAL"), ("currency", "TEXT")],
                       "PRIMARY KEY(date, cloud, resource_id)"),
    "vm_metrics": ([("ts", "TEXT"), ("cloud", "TEXT"), ("instance_id", "TEXT"), ("vm", "TEXT"),
                    ("resource_group", "TEXT"), ("size", "TEXT"), ("location", "TEXT"),
                    ("power_state", "TEXT"), ("avg_cpu", "REAL"), ("tag", "TEXT")],
                   "PRIMARY KEY(ts, cloud, instance_id)"),
    "actions": ([("ts", "TEXT"), ("cloud", "TEXT"), ("vm", "TEXT"), ("action", "TEXT"),
                 ("dry_run", "INTEGER"), ("avg_cpu", "REAL"), ("est_saving_per_day", "REAL"),
                 ("result", "TEXT")],
                ""),
}


def _conn():
    return sqlite3.connect(config.DB_PATH)


def _create_sql(name):
    cols, pk = TABLES[name]
    body = ", ".join(f"{n} {t}" for n, t in cols)
    return f"CREATE TABLE IF NOT EXISTS {name}({body}{', ' + pk if pk else ''})"


def init():
    with _conn() as c:
        for name, (cols, _) in TABLES.items():
            new_names = [n for n, _ in cols]
            old_names = [r[1] for r in c.execute(f"PRAGMA table_info({name})")]
            if not old_names:
                c.execute(_create_sql(name))
            elif old_names != new_names:
                _migrate(c, name, old_names, new_names)


def _migrate(c, name, old_names, new_names):
    """Rebuild a table from the Azure-only layout, keeping its rows as cloud='azure'."""
    c.execute(f"ALTER TABLE {name} RENAME TO {name}_old")
    c.execute(_create_sql(name))
    common = [n for n in new_names if n in old_names]
    fill = {"cloud": "'azure'"}
    if name == "vm_metrics":
        fill["instance_id"] = "vm"
    if name == "actions":
        fill["result"] = "note" if "note" in old_names else "NULL"
    extra = [n for n in new_names if n not in common and n in fill]
    cols = common + extra
    select = [n for n in common] + [fill[n] for n in extra]
    c.execute(f"INSERT OR IGNORE INTO {name} ({','.join(cols)}) "
              f"SELECT {','.join(select)} FROM {name}_old")
    c.execute(f"DROP TABLE {name}_old")


def upsert(table, rows):
    if not rows:
        return 0
    cols = list(rows[0])
    q = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    with _conn() as c:
        c.executemany(q, [tuple(r[k] for k in cols) for r in rows])
    return len(rows)


def read(sql, params=()):
    with _conn() as c:
        return pd.read_sql_query(sql, c, params=params)
