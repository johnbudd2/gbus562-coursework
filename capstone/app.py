"""Minimal Streamlit front end for the CDI query agent.

Run from the repository folder, listening on this computer only:
    .venv\\Scripts\\streamlit.exe run capstone\\app.py --server.address localhost --server.headless true
Without --server.address localhost, Streamlit also listens on the local network.

Pick a synthetic case from data/cases (or paste a summary), click "Draft queries",
and read the findings and draft queries. Every click goes through cdi_query's own
request function, so it is logged in runs/log.jsonl exactly like a command-line run.
The API key is read on the server by cdi_query (never sent to the browser).
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdi_query import DEFAULT_MODEL, review, review_text  # noqa: E402

CASES = Path(__file__).resolve().parent / "data" / "cases"
MODELS = [DEFAULT_MODEL, "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]

st.set_page_config(page_title="CDI query drafts", layout="wide")
st.title("CDI query drafts")
st.caption("Synthetic cases only. Every draft is for CDI specialist review; nothing is sent.")

model = st.sidebar.selectbox("Model", MODELS)
source = st.radio("Input", ["Pick a case", "Paste a summary"], horizontal=True)

if source == "Pick a case":
    files = sorted(CASES.glob("case_*.md"))
    chosen = st.selectbox("Case", files, format_func=lambda p: p.name)
    with st.expander("Show case"):
        st.text(chosen.read_text(encoding="utf-8"))
    ready = True
else:
    pasted = st.text_area("Discharge summary, ending with a '## Current codes' section", height=300)
    ready = bool(pasted.strip())

if st.button("Draft queries", type="primary", disabled=not ready):
    stats = {}
    with st.spinner(f"Reviewing with {model}..."):
        try:
            output = review(chosen, model, stats) if source == "Pick a case" else review_text(pasted, model, stats)
        except Exception as e:  # already logged by cdi_query
            st.error(f"{type(e).__name__}: {e}")
            st.stop()
    st.markdown(output)
    st.caption(f"{stats['input_tokens']:,} input tokens · {stats['output_tokens']:,} output tokens · "
               f"{stats['seconds']:.1f} s · logged to runs/log.jsonl")
