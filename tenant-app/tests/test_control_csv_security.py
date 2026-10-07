import csv,io
import pandas as pd
from control_service import _csv_response


def test_csv_formula_cells_and_headers_are_escaped_without_mutating_source():
    frame=pd.DataFrame({"=badheader":["=1+1","  +SUM(A1)","\tformula","\n=1","normal"],"number":[-1,2,3,4,5]})
    original=frame.copy(deep=True)
    content,_=_csv_response(frame,"safe")
    rows=list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert rows[0][0]=="'=badheader"
    assert [row[0] for row in rows[1:]]==["'=1+1","'  +SUM(A1)","'\tformula","'\n=1","normal"]
    assert rows[1][1]=="-1"
    pd.testing.assert_frame_equal(frame,original)
