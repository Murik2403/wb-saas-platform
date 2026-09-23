"""Lossless product finance export; spreadsheet formula inputs remain text."""
import csv
import io
import pandas as pd


def build_finance_csv(products: pd.DataFrame) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")

    def cell(value):
        if pd.isna(value):
            return ""
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    writer.writerow([cell(column) for column in products.columns])
    for row in products.itertuples(index=False, name=None):
        writer.writerow([cell(value) for value in row])
    return output.getvalue().encode("utf-8-sig")
