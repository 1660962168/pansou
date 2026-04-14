import os
import re

def refactor_project_logs(directory='.'):
    # 匹配标准 print() 调用的正则，规避注释和其他包含 print 的字符串
    print_pattern = re.compile(r'(?<!\w)print\((.*)\)')
    
    for root, dirs, files in os.walk(directory):
        # 排除虚拟环境和隐藏目录
        if 'venv' in root or '.git' in root:
            continue
            
        for file in files:
            if file.endswith('.py') and file != os.path.basename(__file__):
                filepath = os.path.join(root, file)
                
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 检查是否存在 print
                if not print_pattern.search(content):
                    continue
                    
                # 将 print(xxx) 替换为 logging.info(xxx)
                new_content = print_pattern.sub(r'logging.info(\1)', content)
                
                # 若文件内没有 import logging 则在顶部注入
                if 'import logging' not in new_content:
                    new_content = 'import logging\n' + new_content
                    
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                    
                print(f"[Refactored] {filepath}")

if __name__ == '__main__':
    refactor_project_logs()