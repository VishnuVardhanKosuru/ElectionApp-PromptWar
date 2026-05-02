"""
Election Process Assistant – Streamlit Frontend v2

Features:
  • Voter Dashboard   – search by address for election info & polling locations
  • Accessibility     – ADA-compliant station filter with wait times
  • Integrity Report  – secure form to log election integrity concerns
  • Calendar          – download .ics file for election day
  • PDF Export        – download election timeline as PDF
"""

import io
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080").rstrip("/")
API = f"{BACKEND_URL}/api/v1"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Election Process Assistant",
    page_icon="🗳️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS – dark navy glassmorphism theme
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif !important; }
.stApp { background: linear-gradient(135deg, #0B1120 0%, #0F172A 60%, #111827 100%); }
.main .block-container { padding: 1.5rem 2rem; max-width: 1200px; }
header[data-testid="stHeader"] { background: transparent; }

.hero {
    background: linear-gradient(135deg, #1E3A8A 0%, #1D4ED8 60%, #2563EB 100%);
    border: 1px solid rgba(96,165,250,0.3);
    border-radius: 16px;
    padding: 2.2rem 2rem 1.8rem;
    text-align: center;
    margin-bottom: 1.8rem;
    box-shadow: 0 0 60px rgba(59,130,246,0.25);
}
.hero h1 { color: #fff; font-size: 2.2rem; font-weight: 700; margin: 0 0 .4rem; }
.hero p  { color: rgba(255,255,255,0.8); font-size: 1rem; margin: 0; }

.card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(59,130,246,0.18);
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
    transition: border-color .2s;
}
.card:hover { border-color: rgba(59,130,246,0.5); }
.card-title { color: #93C5FD; font-weight: 600; font-size: 1rem; margin-bottom: .4rem; }
.card-body  { color: #CBD5E1; font-size: .9rem; line-height: 1.6; }

.badge { display: inline-block; padding: .2rem .7rem; border-radius: 9999px;
         font-size: .78rem; font-weight: 600; margin-right: .3rem; }
.ada-yes { background: rgba(16,185,129,.15); color: #34D399; border: 1px solid #10B981; }
.ada-no  { background: rgba(239,68,68,.15); color: #F87171; border: 1px solid #EF4444; }
.wait-low  { background: rgba(16,185,129,.15); color: #34D399; }
.wait-mod  { background: rgba(245,158,11,.15); color: #FCD34D; }
.wait-high { background: rgba(239,68,68,.15); color: #F87171; }
.wait-vh   { background: rgba(239,68,68,.25); color: #FCA5A5; }
.sev-low  { background:rgba(16,185,129,.1);color:#34D399; }
.sev-med  { background:rgba(245,158,11,.1);color:#FCD34D; }
.sev-high { background:rgba(239,68,68,.1);color:#F87171; }
.sev-crit { background:rgba(220,38,38,.25);color:#FCA5A5; }

.confirm-box {
    background: rgba(16,185,129,.08);
    border: 1px solid #10B981;
    border-radius: 10px;
    padding: 1rem 1.2rem;
    color: #34D399;
}
.error-box {
    background: rgba(239,68,68,.08);
    border: 1px solid #EF4444;
    border-radius: 10px;
    padding: 1rem 1.2rem;
    color: #F87171;
    margin-bottom: .8rem;
}
.section-h { color: #93C5FD; font-size: 1.1rem; font-weight: 600;
             border-bottom: 1px solid rgba(59,130,246,.2);
             padding-bottom: .4rem; margin: 1.2rem 0 .8rem; }

label { color: #CBD5E1 !important; }
button[data-baseweb="tab"] { color: #94A3B8 !important; font-weight: 500; }
button[data-baseweb="tab"][aria-selected="true"] { color: #60A5FA !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------
st.markdown("""
<div class="hero">
  <h1>🗳️ Election Process Assistant</h1>
  <p>Your trusted, non-partisan guide to the democratic process &mdash;
     polling locations, accessibility info, and election integrity reporting.</p>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
for key, default in {
    "election_data": None,
    "address": "",
    "polling_locs": None,
    "wait_data": None,
    "incident_result": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _api(method: str, path: str, **kwargs) -> Optional[Any]:
    """Call the backend API and return parsed JSON or None on error.

    Shows clear error states for every API call failure category.
    """
    try:
        resp = getattr(requests, method)(f"{API}{path}", timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.markdown(
            '<div class="error-box">⚠️ <b>Cannot reach the backend.</b> '
            "Is the FastAPI server running on port 8080?</div>",
            unsafe_allow_html=True,
        )
    except requests.exceptions.Timeout:
        st.markdown(
            '<div class="error-box">⏱️ <b>Request timed out.</b> '
            "The server is taking too long to respond. Please retry.</div>",
            unsafe_allow_html=True,
        )
    except requests.exceptions.HTTPError as e:
        try:
            detail = e.response.json().get("detail", {})
            msg = detail.get("message", str(e)) if isinstance(detail, dict) else str(detail)
            code = detail.get("code", "") if isinstance(detail, dict) else ""
        except Exception:
            msg = str(e)
            code = ""
        st.markdown(
            f'<div class="error-box">🚫 <b>API Error{" [" + code + "]" if code else ""}:</b> '
            f"{msg}</div>",
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.markdown(
            f'<div class="error-box">❌ <b>Unexpected error:</b> {e}</div>',
            unsafe_allow_html=True,
        )
    return None


def _wait_badge(status: str, minutes: int) -> str:
    cls = {"low": "wait-low", "moderate": "wait-mod",
           "high": "wait-high", "very_high": "wait-vh"}.get(status, "wait-low")
    return f'<span class="badge {cls}">~{minutes} min</span>'


def _ada_badge(compliant: bool) -> str:
    if compliant:
        return '<span class="badge ada-yes">♿ ADA Accessible</span>'
    return '<span class="badge ada-no">⚠️ Not Verified</span>'


def _generate_ics(name: str, date_str: str) -> bytes:
    """Generate a minimal RFC-5545 iCalendar file for the election date."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now() + timedelta(days=30)
    dtstart = dt.strftime("%Y%m%d")
    dtend = (dt + timedelta(days=1)).strftime("%Y%m%d")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    uid = str(uuid.uuid4())
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Election Process Assistant//EN\r\n"
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:{stamp}\r\n"
        f"DTSTART;VALUE=DATE:{dtstart}\r\nDTEND;VALUE=DATE:{dtend}\r\n"
        f"SUMMARY:Election Day – {name}\r\n"
        "DESCRIPTION:Don't forget to vote! Contact your local election office for details.\r\n"
        "STATUS:CONFIRMED\r\nTRANSP:TRANSPARENT\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    return ics.encode()


def _generate_pdf(election_data: dict) -> Optional[bytes]:
    """Generate an election timeline PDF using fpdf2.

    Returns raw PDF bytes or None if fpdf2 is not installed.
    """
    try:
        from fpdf import FPDF  # type: ignore[import-untyped]
    except ImportError:
        return None

    election = election_data.get("election") or {}
    contests = election_data.get("contests") or []
    locations = election_data.get("polling_locations") or []
    norm = election_data.get("normalized_input") or {}

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 12, "Election Process Assistant", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "Election Timeline & Ballot Summary", ln=True, align="C")
    pdf.ln(4)
    pdf.set_draw_color(59, 130, 246)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(6)

    # Election details
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 9, election.get("name", "Election Details"), ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 7, f"Election Day: {election.get('election_day', 'N/A')}", ln=True)
    addr_str = ", ".join(filter(None, [
        norm.get("line1"), norm.get("city"), norm.get("state"), norm.get("zip")
    ]))
    if addr_str:
        pdf.cell(0, 7, f"Address: {addr_str}", ln=True)
    pdf.ln(4)

    # Contests
    if contests:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(30, 58, 138)
        pdf.cell(0, 9, "Contests on Your Ballot", ln=True)
        pdf.ln(2)
        for i, c in enumerate(contests[:10], 1):
            office = c.get("office") or c.get("ballot_title") or "Contest"
            candidates = ", ".join(c.get("candidates") or []) or "See ballot"
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(60, 60, 60)
            pdf.cell(0, 7, f"{i}. {office}", ln=True)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 6, f"   Candidates: {candidates}", ln=True)
        pdf.ln(4)

    # Polling locations
    if locations:
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(30, 58, 138)
        pdf.cell(0, 9, "Polling Locations", ln=True)
        pdf.ln(2)
        for loc in locations[:5]:
            addr = loc.get("address") or {}
            a = ", ".join(filter(None, [
                addr.get("line1"), addr.get("city"), addr.get("state")
            ]))
            name = loc.get("name") or "Polling Station"
            hours = loc.get("polling_hours") or "Contact local office"
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(60, 60, 60)
            pdf.cell(0, 7, name, ln=True)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(100, 100, 100)
            if a:
                pdf.cell(0, 6, f"   Address: {a}", ln=True)
            pdf.cell(0, 6, f"   Hours: {hours}", ln=True)
        pdf.ln(4)

    # Footer
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(150, 150, 150)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    pdf.cell(0, 6, f"Generated by Election Process Assistant | {ts} | Non-partisan", align="C")

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "🗳️  Voter Dashboard",
    "♿  Accessibility & Integrity",
    "🚨  Report Incident",
    "📅  Calendar & Export",
])

# ============================================================
# TAB 1 – Voter Dashboard
# ============================================================
with tab1:
    st.markdown('<div class="section-h">Search by Civic Address</div>', unsafe_allow_html=True)

    col_input, col_btn = st.columns([5, 1])
    with col_input:
        address_input = st.text_input(
            "Enter your full address",
            placeholder="e.g. 1600 Amphitheatre Pkwy, Mountain View, CA 94043",
            key="address_input",
            label_visibility="collapsed",
        )
    with col_btn:
        search_clicked = st.button("🔍 Search", use_container_width=True, key="btn_search")

    if search_clicked and address_input.strip():
        with st.spinner("Fetching election data…"):
            data = _api("post", "/elections/info", json={"address": address_input.strip()})
        if data:
            st.session_state.election_data = data
            st.session_state.address = address_input.strip()
            st.session_state.polling_locs = None
            st.session_state.wait_data = None
    elif search_clicked:
        st.warning("Please enter an address before searching.", icon="⚠️")

    data = st.session_state.election_data
    if data:
        election = data.get("election") or {}
        norm = data.get("normalized_input") or {}
        contests = data.get("contests") or []
        locations = data.get("polling_locations") or []

        st.markdown(f"""
        <div class="card">
          <div class="card-title">📋 {election.get('name', 'Election')}</div>
          <div class="card-body">
            📅 <b>Election Day:</b> {election.get('election_day', 'N/A')}<br>
            📍 <b>Your Address:</b> {norm.get('line1','')}, {norm.get('city','')},
               {norm.get('state','')} {norm.get('zip','')}
          </div>
        </div>
        """, unsafe_allow_html=True)

        if contests:
            st.markdown('<div class="section-h">🗳️ Contests on Your Ballot</div>',
                        unsafe_allow_html=True)
            for c in contests[:6]:
                candidates = ", ".join(c.get("candidates") or []) or "See ballot"
                st.markdown(f"""
                <div class="card">
                  <div class="card-title">{c.get('office') or c.get('ballot_title','Contest')}</div>
                  <div class="card-body">👥 Candidates: {candidates}</div>
                </div>
                """, unsafe_allow_html=True)

        if locations:
            st.markdown('<div class="section-h">📍 Polling Locations</div>',
                        unsafe_allow_html=True)
            for loc in locations:
                addr = loc.get("address") or {}
                addr_str = ", ".join(filter(None, [
                    addr.get("line1"), addr.get("city"),
                    addr.get("state"), addr.get("zip"),
                ]))
                st.markdown(f"""
                <div class="card">
                  <div class="card-title">🏛️ {loc.get('name') or 'Polling Station'}</div>
                  <div class="card-body">
                    📍 {addr_str or 'Address not available'}<br>
                    🕐 Hours: {loc.get('polling_hours') or 'Contact your local office'}
                  </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("Enter your address above and click **Search** to get started.", icon="ℹ️")

# ============================================================
# TAB 2 – Accessibility & Integrity Dashboard
# ============================================================
with tab2:
    st.markdown('<div class="section-h">♿ Accessibility & Integrity Dashboard</div>',
                unsafe_allow_html=True)
    st.markdown(
        "<div class='card-body' style='margin-bottom:.8rem'>Filter polling stations to show only "
        "those verified or inferred to be ADA-compliant. Real-time wait times are shown per "
        "station. Always call ahead to confirm accessibility features.</div>",
        unsafe_allow_html=True,
    )

    ada_only = st.toggle("Show ADA-Accessible stations only", value=False, key="ada_toggle")
    use_address = st.session_state.address or ""

    if not use_address:
        st.warning("Search for an address in the **Voter Dashboard** tab first.", icon="⚠️")
    else:
        if st.button("🔄 Load Accessibility Data", use_container_width=False, key="btn_ada"):
            with st.spinner("Fetching accessibility data…"):
                params = {"address": use_address, "accessible_only": str(ada_only).lower()}
                data = _api("get", "/polling-locations", params=params)
            if data:
                st.session_state.polling_locs = data

        locs_data = st.session_state.polling_locs
        if locs_data:
            locations = locs_data.get("locations", [])
            total = locs_data.get("total", 0)
            ada_count = sum(1 for l in locations if l.get("accessibility_compliant"))

            # Integrity metrics
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Stations", total)
            m2.metric("ADA Compliant", ada_count)
            m3.metric("Not Verified", total - ada_count)

            for loc in locations:
                addr = loc.get("address") or {}
                addr_str = ", ".join(filter(None, [
                    addr.get("line1"), addr.get("city"), addr.get("state"),
                ]))
                is_ada = loc.get("accessibility_compliant", False)
                wait = loc.get("wait_minutes", 0)

                if wait < 15:
                    wstatus = "low"
                elif wait < 30:
                    wstatus = "moderate"
                elif wait < 60:
                    wstatus = "high"
                else:
                    wstatus = "very_high"

                st.markdown(f"""
                <div class="card">
                  <div class="card-title">🏛️ {loc.get('name','Polling Station')}</div>
                  <div class="card-body">
                    📍 {addr_str or 'N/A'}<br>
                    🕐 {loc.get('polling_hours') or 'Check with local election office'}<br>
                    {_ada_badge(is_ada)} {_wait_badge(wstatus, wait)}
                  </div>
                </div>
                """, unsafe_allow_html=True)

            # Wait-time detail section
            st.markdown('<div class="section-h">⏱️ Estimated Wait Times</div>',
                        unsafe_allow_html=True)
            if st.button("📊 Refresh Wait Times", key="btn_wait"):
                with st.spinner("Fetching wait-time data…"):
                    wt = _api("get", "/wait-times", params={"address": use_address})
                if wt:
                    st.session_state.wait_data = wt

            wt_data = st.session_state.wait_data
            if wt_data:
                for s in wt_data.get("stations", []):
                    wait_m = s.get("estimated_wait_minutes", 0)
                    st.progress(
                        min(wait_m / 90, 1.0),
                        text=f"{s.get('location_name','Station')} — ~{wait_m} min",
                    )
                st.caption(wt_data.get("note", ""))

# ============================================================
# TAB 3 – Incident Report
# ============================================================
with tab3:
    st.markdown('<div class="section-h">🚨 Report an Election Integrity Incident</div>',
                unsafe_allow_html=True)
    st.markdown(
        "<div class='card-body' style='margin-bottom:.8rem'>"
        "Use this form to report concerns such as voter intimidation, machine malfunctions, "
        "or accessibility barriers. All reports are logged securely and are <b>non-partisan</b>. "
        "For immediate help call <b>1-866-OUR-VOTE</b>.</div>",
        unsafe_allow_html=True,
    )

    with st.form("incident_form", clear_on_submit=True):
        loc_field = st.text_input(
            "Polling Location *",
            placeholder="e.g. Mountain View Community Center, 500 Castro St",
        )
        col_type, col_sev = st.columns(2)
        with col_type:
            incident_type = st.selectbox("Incident Type *", [
                "voter_intimidation", "machine_malfunction", "long_wait_time",
                "accessibility_issue", "poll_worker_misconduct",
                "voter_id_issue", "other",
            ], format_func=lambda x: x.replace("_", " ").title())
        with col_sev:
            severity = st.selectbox("Severity *", ["low", "medium", "high", "critical"],
                                    format_func=str.upper)
        description = st.text_area(
            "Description *",
            placeholder="Please describe what happened in as much detail as possible…",
            height=130,
        )
        st.markdown("**Optional contact information** (for follow-up only):")
        col_name, col_contact = st.columns(2)
        with col_name:
            reporter_name = st.text_input("Your Name (optional)")
        with col_contact:
            reporter_contact = st.text_input("Email or Phone (optional)")

        submitted = st.form_submit_button("📨 Submit Report", use_container_width=True)

    if submitted:
        errors = []
        if not loc_field.strip():
            errors.append("Polling location is required.")
        if len(description.strip()) < 10:
            errors.append("Description must be at least 10 characters.")

        if errors:
            for e in errors:
                st.markdown(f'<div class="error-box">⚠️ {e}</div>', unsafe_allow_html=True)
        else:
            payload = {
                "location": loc_field.strip(),
                "incident_type": incident_type,
                "severity": severity,
                "description": description.strip(),
            }
            if reporter_name.strip():
                payload["reporter_name"] = reporter_name.strip()
            if reporter_contact.strip():
                payload["reporter_contact"] = reporter_contact.strip()

            with st.spinner("Submitting report…"):
                result = _api("post", "/report-incident", json=payload)
            if result:
                st.session_state.incident_result = result

    if st.session_state.incident_result:
        r = st.session_state.incident_result
        st.markdown(f"""
        <div class="confirm-box">
          ✅ <b>Report Received</b><br>
          Incident ID: <code>{r.get('incident_id','')}</code><br>
          {r.get('message','')}
        </div>
        """, unsafe_allow_html=True)

# ============================================================
# TAB 4 – Calendar & PDF Export
# ============================================================
with tab4:
    st.markdown('<div class="section-h">📅 Calendar & Export</div>', unsafe_allow_html=True)

    data = st.session_state.election_data
    if not data:
        st.info(
            "Search for your address in the **Voter Dashboard** tab first to "
            "load your election date.", icon="ℹ️"
        )
    else:
        election = data.get("election") or {}
        election_name = election.get("name", "Election Day")
        election_day = election.get("election_day", "")

        st.markdown(f"""
        <div class="card">
          <div class="card-title">📋 {election_name}</div>
          <div class="card-body">
            📅 Date: <b>{election_day or 'See ballot materials'}</b><br>
            Download your election timeline as a PDF or add a reminder to your calendar.
          </div>
        </div>
        """, unsafe_allow_html=True)

        col_pdf, col_ics = st.columns(2)

        with col_pdf:
            st.markdown("##### 📄 PDF Timeline Export")
            with st.spinner("Generating PDF…"):
                pdf_bytes = _generate_pdf(data)
            if pdf_bytes:
                st.download_button(
                    label="📥 Download Election Timeline (PDF)",
                    data=pdf_bytes,
                    file_name="election_timeline.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="btn_pdf",
                )
            else:
                st.info("Install `fpdf2` in the frontend to enable PDF export.", icon="ℹ️")

        with col_ics:
            st.markdown("##### 📅 Calendar Reminder (.ics)")
            if election_day:
                ics_bytes = _generate_ics(election_name, election_day)
                st.download_button(
                    label="📥 Download Reminder (.ics)",
                    data=ics_bytes,
                    file_name="election_day.ics",
                    mime="text/calendar",
                    use_container_width=True,
                    key="btn_ics",
                )
            else:
                st.warning("Election date not available for calendar export.", icon="⚠️")

        st.markdown("""
        <div class="card" style="margin-top:.6rem">
          <div class="card-title">💡 Voting Tips</div>
          <div class="card-body">
            🕐 Polls typically open 6–7 AM and close 7–8 PM (varies by state).<br>
            🪪 Bring valid photo ID if required in your state.<br>
            ♿ Request accessibility assistance at the polling station.<br>
            📞 Need help? Call <b>1-866-OUR-VOTE</b>.
          </div>
        </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("""
<hr style="border-color:rgba(59,130,246,.15);margin-top:2rem">
<p style="text-align:center;color:#475569;font-size:.8rem">
  Election Process Assistant &bull; Non-partisan &bull; Powered by Google Civic Information API
</p>
""", unsafe_allow_html=True)
