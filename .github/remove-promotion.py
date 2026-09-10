import os
import sys

def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.readlines()

def write_file(file_path, lines):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def remove_lines_containing(lines, text):
    return [line for line in lines if text not in line]

def replace_in_lines(lines, old, new):
    return [line.replace(old, new) for line in lines]

def remove_method_block(lines, method_signature):
    final_lines = []
    skip_block = False
    for line in lines:
        if method_signature in line:
            skip_block = True
            continue
        if skip_block:
            if line.strip() == '}':
                skip_block = False
                continue
        if not skip_block:
            final_lines.append(line)
    return final_lines

def remove_xml_block(lines, identifier):
    result_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if identifier in line:
            if '/>' in line:
                start = i
                while start > 0 and '<MenuItem' not in lines[start]:
                    start -= 1
                i += 1
                continue
            else:
                start = i
                while start > 0 and '<MenuItem' not in lines[start]:
                    start -= 1
                end = i
                while end < len(lines) and '</MenuItem>' not in lines[end]:
                    end += 1
                if end < len(lines):
                    end += 1
                while len(result_lines) > 0 and result_lines[-1] == lines[start]:
                    result_lines.pop()
                    start += 1
                i = end
                continue
        result_lines.append(line)
        i += 1
    return result_lines

def remove_menu_block(lines, identifier):
    start_index = -1
    end_index = -1
    for i, line in enumerate(lines):
        if identifier in line:
            start_index = i
            while start_index >= 0 and '<Menu' not in lines[start_index].strip():
                start_index -= 1
            end_index = i
            while end_index < len(lines):
                if '<Separator' in lines[end_index]:
                    end_index += 1
                    break
                end_index += 1
            break
    if start_index >= 0 and end_index >= 0:
        return lines[:start_index] + lines[end_index:]
    return lines

def remove_duplicate_menu_lines(lines):
    result_lines = []
    prev_line = None
    for line in lines:
        if line.strip() != prev_line:
            result_lines.append(line)
            prev_line = line.strip()
    return result_lines

# 定义要处理的文件和操作
operations = {
    'v2rayN/ServiceLib/Global.cs': [
        # 铁律：不删 PromotionUrl 常量(仅被 MenuPromotion_Click 引用，随菜单一起删)。
        # 保留这个无引用的 public const 编译无碍，却能避免将来上游新增引用时
        # 因删常量而悬空引用、导致编译失败、release 变空(参见安卓版同类事故)。
        ('replace', '"2dust/v2rayN"', '"LOVECHEN/v2rayN-mod"'),
    ],
    'v2rayN/v2rayN.Desktop/Views/MainWindow.axaml': [
        ('remove_xml', 'x:Name="menuPromotion"')
    ],
    'v2rayN/v2rayN.Desktop/Views/MainWindow.axaml.cs': [
        ('remove_lines', 'menuPromotion.Click += MenuPromotion_Click;'),
        ('remove_method', 'private void MenuPromotion_Click')
    ],
    'v2rayN/v2rayN/Views/MainWindow.xaml': [
        ('remove_menu', 'x:Name="menuPromotion"'),
        ('remove_duplicate_menu', None)
    ],
    'v2rayN/v2rayN/Views/MainWindow.xaml.cs': [
        ('remove_lines', 'menuPromotion.Click += MenuPromotion_Click;'),
        ('remove_method', 'private void MenuPromotion_Click')
    ]
}

success = 0
total = len(operations)

for rel_path, ops in operations.items():
    if not os.path.exists(rel_path):
        print(f"文件不存在，跳过: {rel_path}")
        continue
    try:
        lines = read_file(rel_path)
        for op in ops:
            op_type = op[0]
            text = op[1]
            if op_type == 'remove_lines':
                lines = remove_lines_containing(lines, text)
            elif op_type == 'remove_method':
                lines = remove_method_block(lines, text)
            elif op_type == 'remove_xml':
                lines = remove_xml_block(lines, text)
            elif op_type == 'replace':
                lines = replace_in_lines(lines, text, op[2])
            elif op_type == 'remove_menu':
                lines = remove_menu_block(lines, text)
            elif op_type == 'remove_duplicate_menu':
                lines = remove_duplicate_menu_lines(lines)
        write_file(rel_path, lines)
        success += 1
        print(f"✅ {rel_path}")
    except Exception as e:
        print(f"❌ {rel_path}: {e}")

print(f"\n处理完成: {success}/{total}")
if success < total:
    print("⚠️ 部分文件处理失败，但继续执行")