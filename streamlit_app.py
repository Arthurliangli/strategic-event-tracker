# Root-level alias for Streamlit Cloud deployment.
# Streamlit Cloud requires the main file to be at the repo root.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
exec(open(os.path.join(os.path.dirname(__file__), "app", "streamlit_app.py")).read())
