import json

def find_index_by_code_custom(courses, code):
    for i, course in enumerate(courses):
        if course.get("code") == code:
            return i
    return -1

with open('res/custom/test.json', 'r', encoding='utf8') as f:
    custom_array = json.load(f)

print(find_index_by_code_custom(custom_array, "251001_PKB102_12"))