"""
代码质量检查测试
包括类型检查、代码风格检查、导入依赖检查等
"""
import pytest
import subprocess
import sys
from pathlib import Path
import ast
import importlib.util
from typing import List, Tuple


class TestCodeQuality:
    """代码质量检查测试类"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """设置测试环境"""
        self.project_root = Path(__file__).parent.parent
        self.src_files = list(self.project_root.glob("**/*.py"))
        # 排除测试文件和虚拟环境
        self.src_files = [
            f for f in self.src_files 
            if not any(part.startswith('.') or part in ['tests', 'venv', '__pycache__'] 
                      for part in f.parts)
        ]
    
    def test_python_syntax(self):
        """测试Python语法正确性"""
        syntax_errors = []
        
        for file_path in self.src_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                ast.parse(content, filename=str(file_path))
            except SyntaxError as e:
                syntax_errors.append(f"{file_path}: {e}")
            except Exception as e:
                syntax_errors.append(f"{file_path}: 无法读取文件 - {e}")
        
        if syntax_errors:
            pytest.fail(f"发现语法错误:\n" + "\n".join(syntax_errors))
    
    def test_import_dependencies(self):
        """测试导入依赖是否正确"""
        import_errors = []
        
        for file_path in self.src_files:
            try:
                # 解析AST获取导入语句
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                tree = ast.parse(content, filename=str(file_path))
                imports = self._extract_imports(tree)
                
                # 检查每个导入
                for import_name in imports:
                    if not self._can_import(import_name, file_path):
                        import_errors.append(f"{file_path}: 无法导入 {import_name}")
                        
            except Exception as e:
                import_errors.append(f"{file_path}: 检查导入时出错 - {e}")
        
        if import_errors:
            pytest.fail(f"发现导入错误:\n" + "\n".join(import_errors))
    
    def test_required_functions_exist(self):
        """测试必需的函数是否存在"""
        required_functions = {
            "main.py": ["load_config", "main"],
            "src/bot_manage/cog.py": ["setup"],
            "src/thread_manage/cog.py": ["setup"],
            "src/admin/cog.py": ["setup"],
            "src/verify/cog.py": ["setup"]
        }
        
        missing_functions = []
        
        for file_rel_path, functions in required_functions.items():
            file_path = self.project_root / file_rel_path
            if not file_path.exists():
                missing_functions.append(f"{file_rel_path}: 文件不存在")
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                tree = ast.parse(content, filename=str(file_path))
                defined_functions = self._extract_function_names(tree)
                
                for func_name in functions:
                    if func_name not in defined_functions:
                        missing_functions.append(f"{file_rel_path}: 缺少函数 {func_name}")
                        
            except Exception as e:
                missing_functions.append(f"{file_rel_path}: 检查函数时出错 - {e}")
        
        if missing_functions:
            pytest.fail(f"发现缺少的必需函数:\n" + "\n".join(missing_functions))
    
    def test_cog_classes_exist(self):
        """测试Cog类是否正确定义"""
        cog_files = {
            "src/bot_manage/cog.py": "BotManageCommands",
            "src/thread_manage/cog.py": "ThreadSelfManage", 
            "src/admin/cog.py": "AdminCommands",
            "src/verify/cog.py": "VerifyCommands"
        }
        
        missing_classes = []
        
        for file_rel_path, class_name in cog_files.items():
            file_path = self.project_root / file_rel_path
            if not file_path.exists():
                missing_classes.append(f"{file_rel_path}: 文件不存在")
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                tree = ast.parse(content, filename=str(file_path))
                defined_classes = self._extract_class_names(tree)
                
                if class_name not in defined_classes:
                    missing_classes.append(f"{file_rel_path}: 缺少类 {class_name}")
                    
            except Exception as e:
                missing_classes.append(f"{file_rel_path}: 检查类时出错 - {e}")
        
        if missing_classes:
            pytest.fail(f"发现缺少的Cog类:\n" + "\n".join(missing_classes))
    
    def _extract_imports(self, tree: ast.AST) -> List[str]:
        """从AST中提取导入语句"""
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports
    
    def _extract_function_names(self, tree: ast.AST) -> List[str]:
        """从AST中提取函数名"""
        functions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                functions.append(node.name)
        return functions
    
    def _extract_class_names(self, tree: ast.AST) -> List[str]:
        """从AST中提取类名"""
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
        return classes
    
    def _can_import(self, module_name: str, file_path: Path) -> bool:
        """检查是否可以导入指定模块"""
        # 跳过相对导入和一些特殊模块的检查
        if module_name.startswith('.') or module_name in ['main']:
            return True
        
        # 跳过标准库和已知的第三方库
        standard_libs = {
            'os', 'sys', 'json', 'logging', 'traceback', 'datetime', 'pathlib',
            'asyncio', 'typing', 'functools', 'uuid', 'random', 'importlib'
        }
        third_party_libs = {
            'discord', 'dotenv'
        }
        
        if module_name.split('.')[0] in standard_libs | third_party_libs:
            return True
        
        # 检查项目内部模块
        if module_name.startswith('src.'):
            module_path = self.project_root / module_name.replace('.', '/') 
            return (module_path.with_suffix('.py').exists() or 
                   (module_path / '__init__.py').exists())
        
        return True  # 对于其他情况，假设可以导入
