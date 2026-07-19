@echo off
cd /d C:\FutureMathics.ai
set PYTHONPATH=C:\FutureMathics.ai
python -m streamlit run scripts\sandbox_streamlit.py --server.port 8502 --server.address 127.0.0.1
