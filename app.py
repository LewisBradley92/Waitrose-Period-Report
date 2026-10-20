    "Order": "order_internal_id",
    "Visit": "internal_id",
    "Site": "site_internal_id",
    "Premises Name": "site_name",
    "Address1": "site_address_1",
    "Address2": "site_address_2",
    "Address3": "site_address_3",
    "City": "site_address_3",
    "Post Code": "site_post_code",
    "Item to order": item_column,
    "Actual Visit Date": "date_of_visit_local",
    "Actual Visit Time": "time_of_visit_local",
    "Pass-Fail": "primary_result",
    "Pass-Fail2": "secondary_result",
    "Abort Reason": "Please detail why you were unable to conduct this audit:",
    "Site Code": "site_code",
    "Auditor Gender": "auditor_gender",
    "Extra Site 3": "Extra Site 3",
    "Extra Site 4": "Extra Site 4",
}

# Locate the start of the actual audit question block. Export metadata before
# this point is excluded; the fixed A:T fields above are always retained.
first_question = next(
    (
        i for i, column in enumerate(raw.columns)
        if column.casefold().startswith("were you able to successfully conduct this audit")
        or column.casefold().startswith("please detail why you were unable to conduct this audit")
    ),
    None,
)
if first_question is None:
    st.error("Could not identify the audit-question columns in this export. Please check the CSV format.")
    st.stop()

question_columns = list(raw.columns[first_question:])
# Only include actual question fields; don't duplicate any of the fixed columns.
question_columns = [c for c in question_columns if c not in FIXED_COLUMN_MAP.values()]

def build_report(source):
    report = pd.DataFrame(index=source.index)
    for target in FIXED_COLUMNS:
        source_column = FIXED_COLUMN_MAP.get(target, target)
        if source_column in source.columns:
            report[target] = source[source_column]
        else:
            report[target] = pd.NA

    # AM / PM is a stable report field, derived from the current visit time.
    def to_ampm(value):
        if pd.isna(value):
            return pd.NA
        parsed = pd.to_datetime(str(value), errors="coerce")
        if pd.isna(parsed):
            return pd.NA
        return "AM" if parsed.hour < 12 else "PM"

    report["AM / PM"] = source["time_of_visit_local"].apply(to_ampm) if "time_of_visit_local" in source.columns else pd.NA

    # Current-period question columns follow the fixed A:T block.
    for column in question_columns:
        if column in source.columns and column not in report.columns:
            report[column] = source[column]
    return report.reset_index(drop=True)

main_report = build_report(raw.loc[~odp_mask].copy())
odp_report = build_report(raw.loc[odp_mask].copy())

# Summary counts for the two report tabs.
c1, c2, c3 = st.columns(3)
c1.metric("Total audits in export", f"{len(raw):,}")
c2.metric("Main report", f"{len(main_report):,}")
c3.metric("ODPs — Rapid Delivery", f"{len(odp_report):,}")

st.caption(
    f"Columns A:T are fixed to the standard report layout. "
    f"{len(question_columns):,} current-period question columns are appended after column T; "
    "administrative export metadata is excluded."
)

tab_main, tab_odp = st.tabs(["Main report", "ODPs"])

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

# Excel output contains exactly the two report sheets.
buffer = io.BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    main_report.to_excel(writer, sheet_name="Main Report", index=False)
    odp_report.to_excel(writer, sheet_name="ODPs", index=False)

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


