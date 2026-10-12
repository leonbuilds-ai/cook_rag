"""Session-local history and follow-up resolution based on the actual displayed answer."""
import json
import re
import logging
from dataclasses import dataclass
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage


NUMBERS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
logger = logging.getLogger(__name__)


def number(text):
    return int(text) if text.isdigit() else NUMBERS.get(text)


@dataclass
class FollowUp:
    query: str
    clarification: str = ''


@dataclass
class Turn:
    question: str
    answer: str
    choices: dict


class ConversationMemory:
    def __init__(self, max_turns=6, max_chars=6000):
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.turns = []

    def clear(self):
        self.turns.clear()

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
        choices = {}
        for line in answer.splitlines():
            match = re.match(r'^\s*(?:#{1,6}\s*)?(?:\*\*)?[（(]?(\d+|[一二三四五六七八九十])[.)、：:）)]\s*(.*)', line)
            if match:
                hits = [n for n in mentioned if n in match[2]]
                if len(hits) == 1:
                    choices[number(match[1])] = hits[0]
        if not choices:
            choices = dict(enumerate(mentioned, 1))
        if not choices and len(names) == 1:
            choices = {1: next(iter(names))}
        self.turns.append(Turn(question[:1000], answer[:self.max_chars], choices))
        self.turns = self.turns[-self.max_turns:]

    def resolve(self, question, known_names, llm):
        # Explicit dish names indicate a self-contained query or a topic change.
        if any(name and name in question for name in known_names):
            return FollowUp(question)
        ordinal = re.search(r'第\s*(\d+|[一二三四五六七八九十两])\s*(?:个|道|种|款|项)', question)
        choices = self.turns[-1].choices if self.turns else {}
        if ordinal:
            selected = choices.get(number(ordinal[1]))
            if not selected:
                return FollowUp(question, '无法确定你选择的是哪道菜，请直接告诉我菜名，或选择上一条回答中有效的编号。')
            if re.search(r'怎么|如何|多少|需要|食材|材料|步骤|用量|多久|能|可以|为什么', question):
                return FollowUp(f'{selected}：{question}')
            return FollowUp(f'{selected}怎么做？用户选择：{question}')
        if not self.turns:
            if re.search(r'刚才|上面|上一条|那个|这个|这道|它', question):
                return FollowUp(question, '当前会话还没有可参考的菜谱，请先告诉我菜名。')
            return FollowUp(question)
        related = re.search(r'刚才|上面|上一条|那个|这个|这道|它|继续|多少|多久|几分钟|食材|材料|用量|步骤|放盐|放油', question)
        if related and len(set(choices.values())) == 1:
            return FollowUp(f'{next(iter(choices.values()))}：{question}')
        if len(set(choices.values())) > 1 and re.fullmatch(r'(?:这个|那个|它|这道菜)(?:怎么做|呢|的做法)?[？?。]*', question):
            return FollowUp(question, '你指的是上一条回复中的哪道菜？请告诉我菜名或编号。')
        prompt = SystemMessage(content='''将最新提问改写为可以独立检索的食谱问题。
历史仅用于解析追问、指代和用户已提出的条件。新话题保持原意，不要附带旧菜名或旧限制。
列表编号以助手实际回复为准；不得根据检索排序猜测。无法唯一确定指代时请求用户澄清。
不要回答烹饪问题，不要添加未提及的食材、菜品或条件。
只输出 JSON：{"query":"独立问题", "clarification":""}。
如需澄清，clarification 填写简短问题，query 保留最新提问。''')
        try:
            response = llm.invoke([prompt, *self.messages(), HumanMessage(content=question)])
            text = response.content.strip()
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
            result = json.loads(text)
            query, clarification = result.get('query'), result.get('clarification', '')
            if not isinstance(query, str) or not query.strip() or len(query) > 1000 or not isinstance(clarification, str):
                raise ValueError('Invalid follow-up resolution')
            return FollowUp(query.strip(), clarification[:500])
        except Exception:
            logger.warning('追问解析不可用，使用明确菜名或请求澄清')
            if related:
                return FollowUp(question, '你指的是上一条回复中的哪道菜？请告诉我菜名或编号。')
            return FollowUp(question)
