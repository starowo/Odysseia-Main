# Discord机器人项目Makefile

.PHONY: help install test test-all test-quality test-reload test-cogs test-config clean coverage lint format

# 默认目标
help:
	@echo "可用的命令:"
	@echo "  install      - 安装项目依赖"
	@echo "  test         - 运行所有测试"
	@echo "  test-quality - 运行代码质量检查"
	@echo "  test-reload  - 运行热重载测试"
	@echo "  test-cogs    - 运行Cog功能测试"
	@echo "  test-config  - 运行配置系统测试"
	@echo "  coverage     - 生成覆盖率报告"
	@echo "  lint         - 运行代码检查"
	@echo "  format       - 格式化代码"
	@echo "  clean        - 清理临时文件"

# 安装依赖
install:
	pip install -r requirements.txt
	pip install -r requirements-test.txt

# 运行所有测试
test:
	python run_tests.py all -v

# 运行特定类型的测试
test-quality:
	python run_tests.py quality -v

test-reload:
	python run_tests.py reload -v

test-cogs:
	python run_tests.py cogs -v

test-config:
	python run_tests.py config -v

# 使用pytest运行所有测试
test-all:
	python run_tests.py pytest -v

# 生成覆盖率报告
coverage:
	pytest --cov=src --cov=main --cov-report=html --cov-report=term

# 代码检查
lint:
	flake8 src/ main.py --max-line-length=100 --ignore=E203,W503
	mypy src/ main.py --ignore-missing-imports

# 代码格式化
format:
	black src/ main.py tests/ --line-length=100
	isort src/ main.py tests/

# 清理临时文件
clean:
	rm -rf __pycache__/
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf test_report.json
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
