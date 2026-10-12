"""Conservative taste screening against complete source recipes, without model guesses."""
import re


def wants_light(query):
    positive = re.sub(r'(?:不要|不用|不想|不需要|不喜欢|不必)(?:太|很)?(?:清淡|少油|低油)', '', query)
    return bool(re.search(r'清淡|少油|低油', positive))


def light_evidence(doc):
    content = doc.page_content
    name = doc.metadata.get('dish_name', '')
    if re.search(r'不建议[^\n]{0,30}清淡|不可能[^\n]{0,30}清淡|不适合[^\n]{0,30}清淡', content):
        return ''
    if re.search(r'小米辣|朝天椒|泡椒|野山椒|干辣椒|辣椒粉|辣椒油|油泼辣子|豆瓣酱|藤椒油|麻辣|香辣|油炸|炸至|炸制|宽油|红油|重口', content):
        return ''
    match = re.search(r'清淡|少油|低油', content)
    if match:
        return f'原文提到{match[0]}'
    match = re.search(r'清蒸|白灼|蒸', name)
    if match:
        return f'原文采用{match[0]}做法'
    if doc.metadata.get('category') == '汤品' and re.search(r'煮|炖', content):
        return '原文为煮汤或炖汤做法'
    return ''
