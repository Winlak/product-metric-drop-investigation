.PHONY: install lint test data analysis report notebook validate check

install:
	python -m pip install -r requirements.lock

lint:
	python -m ruff check .

test:
	python -m pytest -q

data:
	python scripts/generate_synthetic_data.py --output-dir data/generated

analysis: data
	python scripts/run_analysis.py --data-dir data/generated --output-dir reports

report: analysis
	python scripts/render_report.py --metrics reports/metrics.json --quality reports/quality_checks.json --output reports/analysis_report.md

notebook: data
	python -m jupyter nbconvert --execute --to notebook --inplace notebooks/conversion_drop_rca.ipynb --ExecutePreprocessor.timeout=180

validate: report
	python scripts/validate_case.py --data-dir data/generated --reports-dir reports

check: lint test validate notebook

