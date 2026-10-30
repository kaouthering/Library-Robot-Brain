import os
import tempfile

from library_robot.database import LibraryDB

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(HERE, "library_robot", "library_data.json")


def fresh_db_path():
    """A new database file seeded with the sample library."""
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    db = LibraryDB(path)
    db.init_from_json(SAMPLE)
    db.close()
    return path
