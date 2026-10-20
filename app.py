import io
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Audit Report Builder", page_icon="📋", layout="wide")

st.title("Audit Report Builder")
st.caption(
    "Upload this period's audit CSV to create a report. The report columns are built "
    "from the current export, so changing question sets are supported automatically."
)

with st.sidebar:
    st.header("Current period")
    csv_file = st.file_uploader("Audit export (CSV)", type=["csv"])
    st.divider()
    st.caption(
        "Audits where Item to order is exactly 'Rapid Delivery' are placed on the ODPs tab. "
        "All other audits appear on the main report tab."
    )

if csv_file is None:
    st.info("Upload this period's audit export CSV to get started.")
    st.stop()

try:
    raw = pd.read_csv(csv_file, low_memory=False)
except Exception as exc:
    st.error(f"Could not read the CSV: {exc}")
    st.stop()

# Normalise header whitespace only; retain the current period's question wording.
raw.columns = [str(column).strip() for column in raw.columns]

# Accept common header variations, but require a clear Item to order field.
item_column = next(
    (
        column for column in raw.columns
        if column.casefold().replace("_", " ").strip() == "item to order"
    ),
    None,
)
if item_column is None:
    st.error(
        "The CSV does not contain an 'Item to order' column. "
        "Please check the export's column headers."
    )
    st.stop()

item_values = raw[item_column].fillna("").astype(str).str.strip()
odp_mask = item_values.str.casefold().eq("rapid delivery")

# Each output uses the current export's columns, not a fixed previous-period template.
main_report = raw.loc[~odp_mask].copy().reset_index(drop=True)
odp_report = raw.loc[odp_mask].copy().reset_index(drop=True)

# Keep the raw export available for checking, while reporting the split clearly.
c1, c2, c3 = st.columns(3)
c1.metric("Total audits in export", f"{len(raw):,}")
c2.metric("Main report", f"{len(main_report):,}")
c3.metric("ODPs — Rapid Delivery", f"{len(odp_report):,}")

st.caption(
    f"Detected {len(raw.columns):,} columns in this period's export. "
    "All current-period columns are included in the report; columns/questions not present "
    "in this export are not added."
)

tab_main, tab_odp, tab_raw = st.tabs(["Main report", "ODPs", "Raw export"])

with tab_main:
    st.subheader("Main report")
    st.caption("All exported audits except those with Item to order = Rapid Delivery.")
    st.dataframe(main_report, use_container_width=True, hide_index=True)
    st.download_button(
        "Download main report CSV",
        data=main_report.to_csv(index=False).encode("utf-8-sig"),
        file_name="main_report.csv",
        mime="text/csv",
    )

with tab_odp:
    st.subheader("ODPs")
    st.caption("Only audits where Item to order is Rapid Delivery.")
    st.dataframe(odp_report, use_container_width=True, hide_index=True)
    st.download_button(
        "Download ODPs CSV",
        data=odp_report.to_csv(index=False).encode("utf-8-sig"),
        file_name="odps.csv",
        mime="text/csv",
    )

with tab_raw:
    st.subheader("Raw export")
    st.caption("The complete current-period CSV, before the ODP split.")
    st.dataframe(raw, use_container_width=True, hide_index=True)

# Excel output: two report sheets plus the original complete export for traceability.
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    main_report.to_excel(writer, sheet_name="Main Report", index=False)
    odp_report.to_excel(writer, sheet_name="ODPs", index=False)
    raw.to_excel(writer, sheet_name="Raw Export", index=False)

    # Make the sheets easier to read without changing cell contents.
    for worksheet in writer.book.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column_cells in worksheet.columns:
            max_length = max(
                (len(str(cell.value)) for cell in list(column_cells)[:200] if cell.value is not None),
                default=0,
            )
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 42)

buffer.seek(0)
st.download_button(
    "Download completed Excel report",
    data=buffer.getvalue(),
    file_name=f"audit_report_{datetime.now().strftime('%Y%m%d')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
