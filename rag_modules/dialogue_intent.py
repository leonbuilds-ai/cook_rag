"""Handle standalone social messages without unrelated recipe retrieval."""
import re


def direct_intent(question):
    text = re.sub(r'[\s，。！？、：；,.!?;:~～…]+', '', question).lower()
    if re.fullmatch(r'(?:你好|您好|嗨|哈喽|hello|hi|早上好|早安|下午好|晚上好)(?:呀|啊|哦)?', text):
        return 'greeting'
    if re.fullmatch(r'(?:谢谢|谢谢你|谢谢啦|谢谢您|感谢|谢了|多谢|thanks|thankyou)(?:呀|啊|哦)?', text):
        return 'thanks'
    if text in {'好的', '好哒', '好', '明白了', '知道了', '收到', '嗯', 'ok', 'okay'}:
        return 'acknowledgment'
    if text in {'help', '帮助', '怎么用', '你能做什么', '你可以做什么', '你有什么功能', '使用说明'}:
        return 'help'
    if text in {'有多少食谱', '有多少菜谱', '你有多少食谱', '你有多少菜谱',
                '一共有多少食谱', '一共有多少菜谱', '知识库有多少食谱', '知识库有多少菜谱',
                '你有哪些食谱', '你有哪些菜谱'}:
        return 'catalog'
    return None
