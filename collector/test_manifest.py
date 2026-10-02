import json
import tempfile
import unittest
from pathlib import Path

import manifest


class Manifest(unittest.TestCase):
    def test_lists_only_data_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            up = Path(tmp) / "data" / "film" / "uploads"
            up.mkdir(parents=True)
            (up / "b_cast.csv").write_text("date,text\n")
            (up / "A_marketing.CSV").write_text("date,text\n")
            (up / "notes.docx").write_text("x")
            (up / "README.md").write_text("x")
            (Path(tmp) / "data" / "empty").mkdir()
            self.assertEqual(manifest.build(Path(tmp)), [("film", 2)])
            listed = json.loads((Path(tmp) / "data" / "film" / "files.json").read_text())
            self.assertEqual([f["name"] for f in listed["files"]], ["A_marketing.CSV", "b_cast.csv"])
            self.assertFalse((Path(tmp) / "data" / "empty" / "files.json").exists())
            (up / "b_cast.csv").unlink()
            (up / "A_marketing.CSV").unlink()
            self.assertEqual(manifest.build(Path(tmp)), [("film", 0)])  # an emptied folder clears the list


if __name__ == "__main__":
    unittest.main()
