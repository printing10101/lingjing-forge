"""
自动化测试生成工具 - CLI 入口

支持一键生成和运行测试
"""

import sys
import argparse
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tools.test_generator import CodeAnalyzer, TestGenerator, TestRunner


def cmd_analyze(args):
    """分析代码结构"""
    print(f"分析目录: {args.source}")
    analyzer = CodeAnalyzer(args.source)
    modules = analyzer.analyze_directory()
    
    total_funcs = sum(len(m.functions) for m in modules)
    total_classes = sum(len(m.classes) for m in modules)
    
    print("\n分析结果:")
    print(f"  模块数: {len(modules)}")
    print(f"  函数数: {total_funcs}")
    print(f"  类数: {total_classes}")
    
    if args.verbose:
        print("\n详细列表:")
        for mod in modules:
            print(f"\n  {mod.name}:")
            if mod.functions:
                print(f"    函数: {', '.join(f.name for f in mod.functions)}")
            if mod.classes:
                print(f"    类: {', '.join(c.name for c in mod.classes)}")


def cmd_generate(args):
    """生成测试文件"""
    print(f"生成测试: {args.source} -> {args.output}")
    generator = TestGenerator(args.output)
    files = generator.generate_for_directory(args.source)
    print(f"\n已生成 {len(files)} 个测试文件:")
    for f in files:
        print(f"  {f}")


def cmd_run(args):
    """运行测试"""
    print(f"运行测试: {args.test_dir}")
    runner = TestRunner(args.test_dir)
    report = runner.run(verbose=args.verbose)
    
    print("\n测试报告:")
    print(f"  总计: {report.total}")
    print(f"  通过: {report.passed}")
    print(f"  失败: {report.failed}")
    print(f"  跳过: {report.skipped}")
    print(f"  通过率: {report.pass_rate:.1f}%")


def cmd_all(args):
    """一键生成并运行测试"""
    print("=" * 60)
    print("一键测试生成与运行")
    print("=" * 60)
    
    # 1. 分析
    print("\n[1/3] 分析代码...")
    analyzer = CodeAnalyzer(args.source)
    modules = analyzer.analyze_directory()
    print(f"  发现 {len(modules)} 个模块")
    
    # 2. 生成
    print("\n[2/3] 生成测试...")
    generator = TestGenerator(args.output)
    files = generator.generate_for_directory(args.source)
    print(f"  生成 {len(files)} 个测试文件")
    
    # 3. 运行
    print("\n[3/3] 运行测试...")
    runner = TestRunner(args.output)
    report = runner.run(verbose=args.verbose)
    
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)
    print(f"总计: {report.total}")
    print(f"通过: {report.passed}")
    print(f"失败: {report.failed}")
    print(f"跳过: {report.skipped}")
    print(f"通过率: {report.pass_rate:.1f}%")
    print("=" * 60)
    
    return 0 if report.failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description="自动化测试生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m tools.test_generator analyze python/app
  python -m tools.test_generator generate python/app --output tests/generated
  python -m tools.test_generator run tests/generated
  python -m tools.test_generator all python/app --output tests/generated
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # analyze
    p_analyze = subparsers.add_parser('analyze', help='分析代码结构')
    p_analyze.add_argument('source', help='源代码目录')
    p_analyze.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    
    # generate
    p_generate = subparsers.add_parser('generate', help='生成测试文件')
    p_generate.add_argument('source', help='源代码目录')
    p_generate.add_argument('--output', '-o', default='tests/generated', help='输出目录')
    
    # run
    p_run = subparsers.add_parser('run', help='运行测试')
    p_run.add_argument('test_dir', help='测试目录')
    p_run.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    
    # all
    p_all = subparsers.add_parser('all', help='一键生成并运行')
    p_all.add_argument('source', help='源代码目录')
    p_all.add_argument('--output', '-o', default='tests/generated', help='输出目录')
    p_all.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    
    args = parser.parse_args()
    
    if args.command == 'analyze':
        cmd_analyze(args)
    elif args.command == 'generate':
        cmd_generate(args)
    elif args.command == 'run':
        cmd_run(args)
    elif args.command == 'all':
        sys.exit(cmd_all(args))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
