#!/usr/bin/env python3
"""
Discord机器人项目测试运行器
提供多种测试运行选项和详细的报告
"""
import sys
import subprocess
import argparse
from pathlib import Path
import json
from datetime import datetime


class TestRunner:
    """测试运行器类"""

    def __init__(self):
        self.project_root = Path(__file__).parent
        self.test_dir = self.project_root / "tests"

    def run_code_quality_checks(self, verbose=False):
        """运行代码质量检查"""
        print("🔍 运行代码质量检查...")

        results = {
            'syntax_check': self._run_syntax_check(),
            'import_check': self._run_import_check(),
            'structure_check': self._run_structure_check()
        }

        if verbose:
            self._print_detailed_results(results)

        return all(results.values())

    def run_hot_reload_tests(self, verbose=False):
        """运行热重载功能测试"""
        print("🔄 运行热重载功能测试...")

        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir / "test_hot_reload.py"),
            "-v" if verbose else "-q"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

        if verbose:
            print(result.stdout)
            if result.stderr:
                print("错误输出:", result.stderr)

        return result.returncode == 0

    def run_cog_tests(self, verbose=False):
        """运行Cog功能测试"""
        print("⚙️ 运行Cog功能测试...")

        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir / "test_cogs.py"),
            "-v" if verbose else "-q"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

        if verbose:
            print(result.stdout)
            if result.stderr:
                print("错误输出:", result.stderr)

        return result.returncode == 0

    def run_config_tests(self, verbose=False):
        """运行配置系统测试"""
        print("📋 运行配置系统测试...")

        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir / "test_config_system.py"),
            "-v" if verbose else "-q"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

        if verbose:
            print(result.stdout)
            if result.stderr:
                print("错误输出:", result.stderr)

        return result.returncode == 0

    def run_all_tests(self, verbose=False):
        """运行所有测试"""
        print("🚀 运行完整测试套件...")
        print("=" * 50)

        results = {}

        # 运行代码质量检查
        results['code_quality'] = self.run_code_quality_checks(verbose)
        print()

        # 运行热重载测试
        results['hot_reload'] = self.run_hot_reload_tests(verbose)
        print()

        # 运行Cog测试
        results['cog_tests'] = self.run_cog_tests(verbose)
        print()

        # 运行配置测试
        results['config_tests'] = self.run_config_tests(verbose)
        print()

        # 生成测试报告
        self._generate_test_report(results)

        return all(results.values())

    def run_pytest_all(self, verbose=False):
        """使用pytest运行所有测试"""
        print("🧪 使用pytest运行所有测试...")

        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir),
            "-v" if verbose else "",
            "--tb=short",
            "--color=yes"
        ]

        # 移除空字符串
        cmd = [c for c in cmd if c]

        result = subprocess.run(cmd)
        return result.returncode == 0

    def _run_syntax_check(self):
        """运行语法检查"""
        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir / "test_code_quality.py::TestCodeQuality::test_python_syntax"),
            "-q"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return result.returncode == 0

    def _run_import_check(self):
        """运行导入检查"""
        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir / "test_code_quality.py::TestCodeQuality::test_import_dependencies"),
            "-q"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return result.returncode == 0

    def _run_structure_check(self):
        """运行结构检查"""
        cmd = [
            sys.executable, "-m", "pytest",
            str(self.test_dir / "test_code_quality.py::TestCodeQuality::test_required_functions_exist"),
            "-q"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return result.returncode == 0

    def _print_detailed_results(self, results):
        """打印详细结果"""
        for test_name, passed in results.items():
            status = "✅ 通过" if passed else "❌ 失败"
            print(f"  {test_name}: {status}")

    def _generate_test_report(self, results):
        """生成测试报告"""
        print("📊 测试报告")
        print("=" * 50)

        total_tests = len(results)
        passed_tests = sum(results.values())

        for test_name, passed in results.items():
            status = "✅ 通过" if passed else "❌ 失败"
            print(f"  {test_name.replace('_', ' ').title()}: {status}")

        print("-" * 50)
        print(f"总计: {passed_tests}/{total_tests} 测试通过")

        if passed_tests == total_tests:
            print("🎉 所有测试都通过了！")
        else:
            print("⚠️ 有测试失败，请检查上面的输出")

        # 保存测试报告到文件
        report_data = {
            'timestamp': datetime.now().isoformat(),
            'results': results,
            'summary': {
                'total': total_tests,
                'passed': passed_tests,
                'failed': total_tests - passed_tests
            }
        }

        report_file = self.project_root / "test_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        print(f"📄 详细报告已保存到: {report_file}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Discord机器人项目测试运行器")
    parser.add_argument(
        "test_type",
        nargs="?",
        default="all",
        choices=["all", "quality", "reload", "cogs", "config", "pytest"],
        help="要运行的测试类型"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细输出"
    )

    args = parser.parse_args()

    runner = TestRunner()

    if args.test_type == "all":
        success = runner.run_all_tests(args.verbose)
    elif args.test_type == "quality":
        success = runner.run_code_quality_checks(args.verbose)
    elif args.test_type == "reload":
        success = runner.run_hot_reload_tests(args.verbose)
    elif args.test_type == "cogs":
        success = runner.run_cog_tests(args.verbose)
    elif args.test_type == "config":
        success = runner.run_config_tests(args.verbose)
    elif args.test_type == "pytest":
        success = runner.run_pytest_all(args.verbose)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
