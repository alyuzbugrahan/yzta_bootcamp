#!/bin/bash
source venv/bin/activate 2>/dev/null
cd "$(dirname "$0")"
python main.py
