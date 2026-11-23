import sqlite3
from pathlib import Path
from pymarc import parse_xml_to_array
from src.istaroth_lineage.lineage import resolve_work_uri
from src.naberius_etl.converter import _get_subfield


def init_relational_db(xml_path: str | Path, db_path: str = ":memory:") -> sqlite3.Connection:
    """
    Initialize a normalized SQLite relational database (works, instances, work_subjects)
    to serve as a query benchmark baseline against the RDF Graph Store.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS work_subjects;
        DROP TABLE IF EXISTS instances;
        DROP TABLE IF EXISTS works;

        CREATE TABLE works (
            work_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            ddc TEXT NOT NULL
        );

        CREATE TABLE instances (
            instance_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL,
            title TEXT NOT NULL,
            language TEXT NOT NULL,
            electronic_locator TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            FOREIGN KEY (work_id) REFERENCES works(work_id)
        );

        CREATE TABLE work_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            FOREIGN KEY (work_id) REFERENCES works(work_id)
        );
        """
    )

    records = parse_xml_to_array(str(xml_path))
    for record in records:
        f001 = record.get("001")
        ctrl_num = f001.data.strip() if f001 and f001.data else "unknown"
        f008 = record.get("008")
        field_008 = f008.data if f008 and f008.data else ""
        lang_code = field_008[35:38] if len(field_008) >= 38 else "und"

        author = _get_subfield(record, "100", "a", "Unknown Creator")
        main_title = _get_subfield(record, "245", "a", "Untitled")
        sub_title = _get_subfield(record, "245", "b", "")
        uniform_title = _get_subfield(record, "240", "a", main_title)
        ddc_number = _get_subfield(record, "082", "a", "000")
        electronic_uri = _get_subfield(record, "856", "u", "")

        work_uri = str(resolve_work_uri(uniform_title, author))
        work_id = work_uri.split("/")[-1]

        cur.execute(
            "INSERT OR IGNORE INTO works (work_id, title, author, ddc) VALUES (?, ?, ?, ?)",
            (work_id, uniform_title, author, ddc_number),
        )

        existing_subj = cur.execute(
            "SELECT COUNT(*) FROM work_subjects WHERE work_id = ?", (work_id,)
        ).fetchone()[0]
        if existing_subj == 0:
            for subj_field in record.get_fields("650"):
                if "a" in subj_field:
                    cur.execute(
                        "INSERT INTO work_subjects (work_id, subject) VALUES (?, ?)",
                        (work_id, subj_field["a"].strip()),
                    )

        full_title = f"{main_title}: {sub_title}" if sub_title else main_title
        cur.execute(
            """
            INSERT INTO instances (instance_id, work_id, title, language, electronic_locator, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (ctrl_num, work_id, full_title, lang_code, electronic_uri or None),
        )

    conn.commit()
    return conn


def query_works_sql(conn: sqlite3.Connection, author_filter: str | None = None) -> list[dict]:
    """
    Execute a multi-table SQL JOIN on SQLite to reconstruct the Work -> Instances hierarchy.
    """
    sql = """
        SELECT
            w.work_id,
            w.title AS work_title,
            w.author,
            w.ddc,
            i.instance_id,
            i.title AS instance_title,
            i.language,
            i.electronic_locator,
            i.status
        FROM works w
        JOIN instances i ON w.work_id = i.work_id
    """
    params = []
    if author_filter:
        sql += " WHERE LOWER(w.author) LIKE ?"
        params.append(f"%{author_filter.lower()}%")

    sql += " ORDER BY w.title ASC, i.instance_id ASC"
    rows = conn.execute(sql, params).fetchall()

    works_map: dict[str, dict] = {}
    for row in rows:
        wid = row["work_id"]
        if wid not in works_map:
            subj_rows = conn.execute(
                "SELECT subject FROM work_subjects WHERE work_id = ? ORDER BY subject ASC",
                (wid,),
            ).fetchall()
            works_map[wid] = {
                "work_id": wid,
                "title": row["work_title"],
                "author": row["author"],
                "ddc": row["ddc"],
                "subjects": [r["subject"] for r in subj_rows],
                "editions": [],
            }
        works_map[wid]["editions"].append(
            {
                "instance_id": row["instance_id"],
                "title": row["instance_title"],
                "language": row["language"],
                "electronic_locator": row["electronic_locator"],
                "status": row["status"],
            }
        )

    return list(works_map.values())