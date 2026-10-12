"""Session-local history and follow-up resolution based on the actual displayed answer."""
import json
import re
import logging
from difflib import SequenceMatcher
from dataclasses import dataclass
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage


NUMBERS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
logger = logging.getLogger(__name__)
BARE_NUMBER = r'\s*(?:选择|我选|我要|选|要)?\s*(\d+|[一二三四五六七八九十两])\s*(?:号)?[。！？!?]?\s*'


def number(text):
    return int(text) if text.isdigit() else NUMBERS.get(text)


@dataclass
class FollowUp:
    query: str
    clarification: str = ''
    uses_history: bool = True


@dataclass
class Turn:
    question: str
    answer: str
    choices: dict


@dataclass
class MenuOption:
    title: str
    recipes: tuple


def plain_title(text):
    text = re.sub(r'[（(].*?[）)]', '', text)
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text)


def extract_options(answer, names):
    """Numbered headings own their whole section, including multi-dish combinations."""
    options = {}
    headers = list(re.finditer(r'(?m)^\s{0,3}(#{1,6})\s+(.+)$', answer))
    numbered = r'^(?:\*\*)?[（(]?(\d+|[一二三四五六七八九十])[.)、：:）)]\s*(.+)'
    for i, header in enumerate(headers):
        match = re.match(numbered, header[2])
        if not match:
            continue
        end = len(answer)
        for following in headers[i + 1:]:
            if len(following[1]) <= len(header[1]):
                end = following.start()
                break
        section = answer[header.start():end]
        hits = tuple(sorted((n for n in names if n in section), key=section.index))
        if hits:
            options[number(match[1])] = MenuOption(match[2].strip('* ')[:120], hits)
    if not options:
        for line in answer.splitlines():
            match = re.match(r'^\s{0,3}' + numbered, line)
            if match:
                hits = tuple(sorted((n for n in names if n in match[2]), key=match[2].index))
                if hits:
                    options[number(match[1])] = MenuOption(match[2].strip('* ')[:120], hits)
    return options


class ConversationMemory:
    def __init__(self, max_turns=6, max_chars=6000):
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.turns = []
        # Keep compact recommendation anchors independently of long follow-up answers.
        self.menus = []
        self.pending_recipe = None

    def clear(self):
        self.turns.clear()
        self.menus.clear()
        self.pending_recipe = None

    def snapshot(self):
        return {'turns': [{'question': t.question, 'answer': t.answer, 'choices': t.choices} for t in self.turns],
                'menus': [{str(k): {'title': v.title, 'recipes': list(v.recipes)} for k, v in m.items()}
                          for m in self.menus], 'pending_recipe': self.pending_recipe}

    def restore(self, state):
        self.turns = [Turn(t['question'], t['answer'], {int(k): v for k, v in t['choices'].items()})
                      for t in state.get('turns', [])][-self.max_turns:]
        self.menus = [{int(k): MenuOption(v['title'], tuple(v['recipes'])) for k, v in m.items()}
                      for m in state.get('menus', [])][-self.max_turns:]
        self.pending_recipe = state.get('pending_recipe')
        return self

    def messages(self):
        # Keep complete role pairs, giving the newest turn the largest share of the budget.
        pairs, remaining = [], self.max_chars
        for turn in reversed(self.turns):
            if remaining < 200:
                break
            question = turn.question[:min(500, remaining // 3)]
            answer = turn.answer[:remaining - len(question)]
            pairs.append([HumanMessage(content=question), AIMessage(content=answer)])
            remaining -= len(question) + len(answer)
        return [message for pair in reversed(pairs) for message in pair]

    def remember(self, question, answer, docs):
        names = {d.metadata['dish_name'] for d in docs if d.metadata.get('dish_name')}
        # Detect names in the answer, never use retrieval ranking as the displayed list order.
        mentioned = sorted((n for n in names if n in answer), key=answer.index)
        mentioned = [n for n in mentioned if not any(n != other and n in other for other in mentioned)]
        options = extract_options(answer, mentioned)
        choices = {index: option.recipes[0] for index, option in options.items() if len(option.recipes) == 1}
        menu_request = re.search(r'推荐|吃什么|选项|菜单|有哪些|有什么|吃啥|喝什么', question)
        selection = (re.search(r'第\s*(?:\d+|[一二三四五六七八九十两])\s*(?:个|道|项)|\d+[.)、]', question)
                     or re.fullmatch(BARE_NUMBER, question))
        detail = re.search(r'怎么做|步骤|做法|制作|需要|多少', question)
        is_menu = options and not selection and (menu_request or (len(mentioned) > 1 and not detail))
        if is_menu:
            self.menus.append(options)
            self.menus = self.menus[-self.max_turns:]
        if not choices:
            choices = dict(enumerate(mentioned, 1))
        if not choices and len(names) == 1:
            choices = {1: next(iter(names))}
        self.turns.append(Turn(question[:1000], answer[:self.max_chars], choices))
        self.turns = self.turns[-self.max_turns:]

    def reference_summary(self):
        return '\n'.join(f'推荐列表 {menu_index}：' + '；'.join(
            f'{index}. {option.title} → {"、".join(option.recipes)}'
            for index, option in menu.items()) for menu_index, menu in enumerate(self.menus, 1))

    @staticmethod
    def select(option, question):
        names = '、'.join(option.recipes)
        if re.search(r'怎么|如何|多少|需要|食材|材料|步骤|用量|多久|能|可以|为什么', question):
            return FollowUp(f'{names}：{question}')
        return FollowUp(f'{names}怎么做？')

    def resolve(self, question, known_names, llm):
        if self.pending_recipe:
            pending = self.pending_recipe
            self.pending_recipe = None
            confirmation = question.strip().strip('。！？!?').lower()
            if confirmation in {'是', '是的', '对', '对的', '没错', '确认', '就是这个', '就是它', 'yes', 'y'}:
                return FollowUp(f'{pending}怎么做？')
            if confirmation in {'不是', '不对', 'no', 'n'}:
                return FollowUp(question, '请告诉我准确的菜名，或从推荐列表选择编号。')
        references = re.search(r'刚才|上面|上一条|那个|这个|这道|它|这样|之前|最初|前面|上上|原来|原先|继续|再|换|其他|别的|还有|还是|另一个|第\s*(?:\d+|[一二三四五六七八九十两])\s*(?:个|道|项)|(?<![\d.])\d+[.)、](?!\d)', question)
        # Explicit dish names indicate a self-contained query or a topic change.
        if any(name and name in question for name in known_names):
            return FollowUp(question, uses_history=bool(references))
        # Standalone recommendations are new searches, not an invitation to rewrite old answers.
        if not references and re.search(r'吃什么|吃啥|推荐|有哪些|有什么|喝什么', question):
            return FollowUp(question, uses_history=False)
        ordinal = re.search(r'第\s*(\d+|[一二三四五六七八九十两])\s*(?:个|道|种|款|项)(?!步骤|操作|阶段)', question)
        copied = re.search(r'(?<![\d.])([0-9]+|[一二三四五六七八九十])[.)、]\s*(?!\d)', question)
        bare = re.fullmatch(BARE_NUMBER, question)
        index = number(ordinal[1]) if ordinal else number(copied[1]) if copied else number(bare[1]) if bare else None
        choices = self.turns[-1].choices if self.turns else {}
        # Titles and explicit numbering take priority over the most recently discussed dish.
        normalized = plain_title(question)
        for menu in reversed(self.menus):
            titled = [(i, option) for i, option in menu.items()
                      if len(plain_title(option.title)) >= 2 and plain_title(option.title) in normalized]
            if titled:
                if len(titled) != 1 or (index is not None and index != titled[0][0]):
                    return FollowUp(question, '引用的编号和标题不一致或指向多个选项，请确认菜名或完整选项。')
                return self.select(titled[0][1], question)
        if index is not None:
            if copied:
                label = plain_title(question[copied.end():])
                if label and not re.match(r'^(?:呢|怎么|如何|多少|需要|食材|材料|步骤|用量|多久|能|可以|为什么)', label):
                    return FollowUp(question, '找不到对应的历史选项标题，请确认完整标题或直接告诉我菜名。')
            option = self.menus[-1].get(index) if self.menus else None
            if not option:
                return FollowUp(question, '无法确定你选择的是哪道菜，请直接告诉我菜名，或选择最近推荐列表中有效的编号。')
            return self.select(option, question)
        # A near-matching short dish name must be confirmed before ingredient routing.
        candidate = re.sub(r'^(?:请问|我想吃|我想做|想吃|想做)', '', question.strip())
        candidate = re.sub(r'(?:怎么做|如何做|的做法|做法|制作方法)[？?。！!]*$', '', candidate).strip()
        if re.fullmatch(r'[\u4e00-\u9fff]{4,12}', candidate):
            close = sorted(((SequenceMatcher(None, candidate, name).ratio(), name) for name in known_names
                            if abs(len(name) - len(candidate)) <= 1), reverse=True)
            close = [(score, name) for score, name in close if score >= 0.74]
            if close:
                best = [name for score, name in close[:3] if score >= close[0][0] - 0.03]
                if len(best) == 1:
                    self.pending_recipe = best[0]
                    return FollowUp(question, f'你说的“{candidate}”是否指“{best[0]}”？请回复“是”或完整菜名，确认后我再给你做法。')
                return FollowUp(question, f'菜名可能接近：{"、".join(best)}。请告诉我准确的菜名。')
        if not self.turns:
            if re.search(r'刚才|上面|上一条|那个|这个|这道|它', question):
                return FollowUp(question, '当前会话还没有可参考的菜谱，请先告诉我菜名。')
            return FollowUp(question, uses_history=False)
        related = re.search(r'刚才|上面|上一条|那个|这个|这道|它|继续|多少|多久|几分钟|食材|材料|用量|步骤|放盐|放油', question)
        if not references and not related:
            return FollowUp(question, uses_history=False)
        historical = re.search(r'之前|最初|一开始|前面|最开始|第一次|上上|原来|原先', question)
        if related and not historical and len(set(choices.values())) == 1:
            return FollowUp(f'{next(iter(choices.values()))}：{question}')
        if len(set(choices.values())) > 1 and re.fullmatch(r'(?:这个|那个|它|这道菜)(?:怎么做|呢|的做法)?[？?。]*', question):
            return FollowUp(question, '你指的是上一条回复中的哪道菜？请告诉我菜名或编号。')
        prompt = SystemMessage(content='''将最新提问改写为可以独立检索的食谱问题。
历史仅用于解析追问、指代和用户已提出的条件。新话题保持原意，不要附带旧菜名或旧限制。
列表编号以助手实际回复为准；不得根据检索排序猜测。无法唯一确定指代时请求用户澄清。
不要回答烹饪问题，不要添加未提及的食材、菜品或条件。
只输出 JSON：{"query":"独立问题", "clarification":""}。
如需澄清，clarification 填写简短问题，query 保留最新提问。
引用旧推荐的标题或编号时，应使用对应旧选项，不能默认绑定最近讨论的菜。
已保留的推荐列表映射：\n''' + self.reference_summary())
        try:
            response = llm.invoke([prompt, *self.messages(), HumanMessage(content=question)])
            text = response.content.strip()
            if len(text) > 1500:
                raise ValueError('Follow-up output is not a compact query')
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
            result = json.loads(text)
            query, clarification = result.get('query'), result.get('clarification', '')
            if (not isinstance(query, str) or not query.strip() or len(query) > 200
                    or re.search(r'[\r\n#]|为您推荐以下|食谱库为您|所需食材|制作步骤|(?:回答|特点|答案)[:：]', query)
                    or not isinstance(clarification, str)):
                raise ValueError('Invalid follow-up resolution')
            return FollowUp(query.strip(), clarification[:500])
        except Exception:
            logger.warning('追问解析不可用，使用明确菜名或请求澄清')
            if references or related:
                return FollowUp(question, '你指的是上一条回复中的哪道菜？请告诉我菜名或编号。')
            return FollowUp(question, uses_history=False)
