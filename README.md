# CAS Statements Analyzer

A functional dashboard and analytical tool for deep financial analysis. Designed for financial advisors, this tool ingests complex CAS statements and extracts clear, accurate performance metrics to present to clients. 

It exists to replace tedious Excel crunching with instant, mathematically rigorous Time-Weighted Returns (TWR), XIRR, and Capture Ratios based on official CAS PDF data.

## Features

- **Automated CAS Parsing**: Replaces tedious manual Excel crunching.
- **Advanced Metrics**: Calculates mathematically rigorous Time-Weighted Returns, XIRR, and Capture Ratios.
- **Institutional-Grade UI**: A clean, subdued, professional dashboard that prioritizes clarity, data density, and precision without clutter.

## Setup & Run

### Backend (Python)
Ensure you have Python installed. You can install the dependencies via:
```bash
pip install -r requirements.txt
```

### Frontend (React/Node)
Navigate to the `frontend` directory and install the necessary packages:
```bash
cd frontend
npm install
npm run dev
```

## Structure
- `frontend/`: The frontend application interface.
- Core python files (`main.py`, `metrics.py`, `parser.py`): Backend logic for parsing and financial calculations.
