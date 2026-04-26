import tempfile
import unittest
from pathlib import Path

from csv_parser import StatementCsvParser


TAB_DELIMITED_STATEMENT = """Transaction Date\tPost Date\tDescription\tCategory\tType\tAmount\tMemo
12/13/2025\t12/14/2025\tUEP*MAKAN HOUSE\tFood & Drink\tSale\t-88.39\t
12/12/2025\t12/14/2025\tCVS/PHARMACY #02700\tHealth & Wellness\tSale\t-8.79\t
12/12/2025\t12/14/2025\tNET COST 3100 OCEAN AVE\tGroceries\tSale\t-5.99\t
12/12/2025\t12/14/2025\tBP#34122123010 OCEAN BP\tGas\tSale\t-38.22\t
12/12/2025\t12/14/2025\tROLL-N-ROASTER\tFood & Drink\tSale\t-6.26\t
12/13/2025\t12/14/2025\tTICKETS AT WORK\tEntertainment\tSale\t-25.50\t
12/12/2025\t12/14/2025\tTST* VATO NYC\tFood & Drink\tSale\t-40.27\t
12/12/2025\t12/14/2025\tNYCDOT PARKNYC\tTravel\tSale\t-2.20\t
12/12/2025\t12/14/2025\tSP ALO-YOGA\tShopping\tReturn\t207.20\t
12/11/2025\t12/12/2025\tTST*SUKI DESU\tFood & Drink\tSale\t-32.86\t
12/11/2025\t12/12/2025\tTARGET        00033878\tShopping\tSale\t-2.69\t
"""


class StatementCsvParserTests(unittest.TestCase):
    def test_parse_accepts_tab_delimited_statement_export_with_extra_columns(self):
        parser = StatementCsvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "statement.tsv"
            path.write_text(TAB_DELIMITED_STATEMENT, encoding="utf-8")

            transactions = parser.parse(str(path))

        self.assertEqual(len(transactions), 10)
        self.assertEqual(transactions[0].date, "2025-12-13")
        self.assertEqual(transactions[0].description, "UEP*MAKAN HOUSE")
        self.assertEqual(transactions[0].category, "Food & Drink")
        self.assertEqual(transactions[0].amount, 88.39)
        self.assertTrue(all(item.amount > 0 for item in transactions))
        self.assertFalse(any(item.description == "SP ALO-YOGA" for item in transactions))

