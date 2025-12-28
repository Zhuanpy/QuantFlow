#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
权限修复脚本
用于修复数据目录的权限问题
"""

import os
import sys
import stat
from pathlib import Path

def fix_directory_permissions(directory_path):
    """
    修复目录权限
    
    Args:
        directory_path: 目录路径
    """
    try:
        path = Path(directory_path)
        
        if not path.exists():
            print(f"目录不存在: {directory_path}")
            return False
        
        # 修复目录权限
        os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        print(f"已修复目录权限: {directory_path}")
        
        # 修复目录下所有文件的权限
        for file_path in path.rglob('*'):
            if file_path.is_file():
                try:
                    os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
                    print(f"已修复文件权限: {file_path}")
                except Exception as e:
                    print(f"修复文件权限失败: {file_path}, 错误: {e}")
        
        return True
        
    except Exception as e:
        print(f"修复目录权限失败: {directory_path}, 错误: {e}")
        return False

def main():
    """主函数"""
    # 获取项目根目录
    project_root = Path(__file__).parent
    
    # 需要修复权限的目录
    directories_to_fix = [
        project_root / 'data',
        project_root / 'data' / 'data',
        project_root / 'data' / 'data' / 'quarters',
        project_root / 'data' / 'data' / 'quarters' / '2025',
        project_root / 'data' / 'data' / 'quarters' / '2025' / 'Q4',
    ]
    
    print("开始修复权限...")
    
    success_count = 0
    for directory in directories_to_fix:
        if fix_directory_permissions(directory):
            success_count += 1
    
    print(f"\n权限修复完成！成功修复 {success_count}/{len(directories_to_fix)} 个目录")
    
    # 检查特定文件
    test_file = project_root / 'data' / 'data' / 'quarters' / '2025' / 'Q4' / '002475.csv'
    if test_file.exists():
        try:
            # 尝试写入测试
            with open(test_file, 'a') as f:
                f.write('\n# 权限测试\n')
            print(f"文件 {test_file} 权限正常")
        except PermissionError:
            print(f"文件 {test_file} 仍然没有写权限")
        except Exception as e:
            print(f"测试文件权限时出错: {e}")

if __name__ == '__main__':
    main()
