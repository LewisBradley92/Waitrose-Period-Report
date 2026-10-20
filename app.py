import io
from datetime import datetime
from difflib import SequenceMatcher

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
    csv_file = st.file_uploader("Audit export (CSV)", type=["csv"], key="audit_csv")
    waitrose_file = st.file_uploader("Waitrose site export (CSV)", type=["csv"], key="waitrose_sites_csv")
    st.divider()
    st.caption(
        "Audits where Item to order is exactly 'Rapid Delivery' are placed on the ODPs tab. "
        "All other audits appear on the main report tab."
    )

if csv_file is None or waitrose_file is None:
    missing_files = []
    if csv_file is None:
        missing_files.append("this period's audit export CSV")
    if waitrose_file is None:
        missing_files.append("the Waitrose site export CSV")
    st.info(
        "Please upload both files to generate the report. Still needed: "
        + " and ".join(missing_files)
        + "."
    )
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

# Columns A:T are fixed to the established report layout (from the supplied
# previous-period workbook). New/changed question columns are appended after T.
FIXED_COLUMNS = [
    "Order", "Visit", "Site", "Premises Name", "Address1", "Address2",
    "Address3", "City", "Post Code", "Item to order", "Actual Visit Date",
    "Actual Visit Time", "AM / PM", "Pass-Fail", "Pass-Fail2", "Abort Reason",
    "Site Code", "Auditor Gender", "Extra Site 3", "Extra Site 4",
]

FIXED_COLUMN_MAP = {
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

def build_report(source, columns_to_include):
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

    # Column N is Pass-Fail; always uppercase its values.
    if "Pass-Fail" in report.columns:
        report["Pass-Fail"] = report["Pass-Fail"].map(
            lambda value: value.upper() if isinstance(value, str) else value
        )

    # Current-period question columns follow the fixed A:T block.
    for column in columns_to_include:
        if column in source.columns and column not in report.columns:
            report[column] = source[column]
    return report.reset_index(drop=True)

main_source = raw.loc[~odp_mask].copy()
odp_source = raw.loc[odp_mask].copy()

main_report = build_report(main_source, question_columns)

# For ODPs, keep only question columns that have an actual answer in at least
# one Rapid Delivery audit. This prevents unrelated/unused questions from
# appearing on the ODPs tab while preserving the fixed A:T report layout.
def has_answer(series):
    values = series.dropna().astype(str).str.strip()
    return values.ne("").any()

odp_question_columns = [
    column for column in question_columns
    if column in odp_source.columns and has_answer(odp_source[column])
]
odp_report = build_report(odp_source, odp_question_columns)

# Optional Waitrose site lookup is applied to ODPs only. Match by the
# auditor-provided store name first, then by postcode; unmatched rows stay as-is.
if waitrose_file is not None:
    try:
        sites = pd.read_csv(waitrose_file, low_memory=False)
        sites.columns = [str(column).strip() for column in sites.columns]
        required_site_columns = {"client_name", "name", "address_1", "address_2", "address_3", "post_code", "code"}
        if not required_site_columns.issubset(set(sites.columns)):
            st.error(
                "The Waitrose site export is missing required columns: "
                + ", ".join(sorted(required_site_columns - set(sites.columns)))
            )
        else:
            # Restrict all matching to the requested client only. This prevents
            # similarly named stores belonging to other clients being selected.
            sites = sites.loc[
                sites["client_name"].fillna("").astype(str).str.strip().str.casefold()
                == "waitrose supermarkets".casefold()
            ].copy()
            if sites.empty:
                st.error(
                    "No sites with client name 'Waitrose Supermarkets' were found in the site export. "
                    "Please check the client_name column and upload the correct export."
                )
                st.stop()
            def normalise_match(value):
                if pd.isna(value):
                    return ""
                return "".join(ch.lower() for ch in str(value).strip() if ch.isalnum())

            def normalise_postcode(value):
                # Trim leading/trailing whitespace and ignore spaces/punctuation
                # so auditor-entered postcodes match the site export reliably.
                return "".join(ch for ch in normalise_match(value) if ch.isalnum())

            def fuzzy_name_match(name_key, site_rows, name_lookup):
                """Return a unique exact/fuzzy site-name match, or None if ambiguous."""
                if not name_key:
                    return None
                exact = name_lookup.get(name_key)
                if exact is not None:
                    return exact

                # Allow small differences such as missing commas, extra words,
                # or minor spelling/spacing variations, but avoid weak matches.
                scored = []
                for _, candidate in site_rows[site_rows["_name_key"].ne("")].iterrows():
                    candidate_key = candidate["_name_key"]
                    score = SequenceMatcher(None, name_key, candidate_key).ratio()
                    # Also score containment generously (e.g. a shortened store name).
                    if name_key in candidate_key or candidate_key in name_key:
                        score = max(score, min(len(name_key), len(candidate_key)) / max(len(name_key), len(candidate_key)))
                    if score >= 0.84:
                        scored.append((score, candidate))
                if not scored:
                    return None
                scored.sort(key=lambda item: item[0], reverse=True)
                best_score = scored[0][0]
                best = [row for score, row in scored if abs(score - best_score) < 0.015]
                # Never guess when multiple sites are equally plausible.
                return best[0] if len(best) == 1 else None

            store_name_question = next(
                (c for c in odp_question_columns
                 if c.strip().casefold() == "please confirm the name of the waitrose store you tested:"),
                None,
            )
            store_postcode_question = next(
                (c for c in odp_question_columns
                 if c.strip().casefold() == "please confirm the postcode of the waitrose store you tested:"),
                None,
            )

            if store_name_question is None and store_postcode_question is None:
                st.warning(
                    "The ODP audit export does not contain the Waitrose store name or postcode questions, "
                    "so the site details have been left unchanged."
                )
            else:
                sites = sites.copy()
                # The site export can contain repeated rows for the same site.
                # Collapse exact duplicate site codes before building lookups;
                # otherwise a valid match (e.g. Waitrose Egham) is incorrectly
                # treated as ambiguous just because the site appears twice.
                sites["code"] = sites["code"].astype("string").str.strip()
                sites = sites.drop_duplicates(subset=["code"], keep="first")
                sites["_name_key"] = sites["name"].map(normalise_match)
                sites["_postcode_key"] = sites["post_code"].map(normalise_postcode)
                # Only use unique keys across distinct sites, so genuinely
                # ambiguous names/postcodes never overwrite site details.
                name_lookup = {
                    key: group.iloc[0]
                    for key, group in sites[sites["_name_key"].ne("")].groupby("_name_key")
                    if group["code"].nunique(dropna=False) == 1
                }
                postcode_lookup = {
                    key: group.iloc[0]
                    for key, group in sites[sites["_postcode_key"].ne("")].groupby("_postcode_key")
                    if group["code"].nunique(dropna=False) == 1
                }

                for row_idx in odp_report.index:
                    matched_site = None
                    if store_name_question and store_name_question in odp_source.columns:
                        name_key = normalise_match(odp_source.iloc[row_idx][store_name_question])
                        if name_key:
                            matched_site = fuzzy_name_match(name_key, sites, name_lookup)
                    if matched_site is None and store_postcode_question and store_postcode_question in odp_source.columns:
                        postcode_key = normalise_postcode(odp_source.iloc[row_idx][store_postcode_question])
                        if postcode_key:
                            matched_site = postcode_lookup.get(postcode_key)

                    if matched_site is not None:
                        odp_report.at[row_idx, "Premises Name"] = matched_site["name"]
                        odp_report.at[row_idx, "Address1"] = matched_site["address_1"]
                        odp_report.at[row_idx, "Address2"] = matched_site["address_2"]
                        odp_report.at[row_idx, "Address3"] = matched_site["address_3"]
                        odp_report.at[row_idx, "City"] = matched_site["city"] if "city" in sites.columns else pd.NA
                        odp_report.at[row_idx, "Post Code"] = matched_site["post_code"]
                        odp_report.at[row_idx, "Site Code"] = matched_site["code"]
    except Exception as exc:
        st.error(f"Could not read the Waitrose site export: {exc}")

# Summary counts for the two report tabs.
c1, c2, c3 = st.columns(3)
c1.metric("Total audits in export", f"{len(raw):,}")
c2.metric("Main report", f"{len(main_report):,}")
c3.metric("ODPs — Rapid Delivery", f"{len(odp_report):,}")

st.caption(
    f"Columns A:T are fixed to the standard report layout. "
    f"{len(question_columns):,} current-period question columns are available for the main report. "
    f"The ODPs tab includes only question columns answered in Rapid Delivery audits. "
    "Administrative export metadata is excluded."
)

tab_main, tab_odp = st.tabs(["Main report", "ODPs"])

with tab_main:
    st.subheader("Main report")
    st.caption("All exported audits except those with Item to order = Rapid Delivery.")
    st.dataframe(main_report, use_container_width=True, hide_index=True)
with tab_odp:
    st.subheader("ODPs")
    st.caption("Only audits where Item to order is Rapid Delivery. If a Waitrose site export is uploaded, matched site details replace columns D–I and Q; unmatched rows remain unchanged.")
    st.dataframe(odp_report, use_container_width=True, hide_index=True)
# One download button provides an Excel workbook containing exactly the two report sheets.
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
    "Download completed report (Excel)",
    data=buffer.getvalue(),
    file_name=f"audit_report_{datetime.now().strftime('%Y%m%d')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
