"""Offline Ragas replay of captured answers; SQL caches exact samples and judge profiles."""
import argparse
import hashlib
import json
import math
import time
import logging
from pathlib import Path
from sqlalchemy import select
from config import DEFAULT_CONFIG, ROOT
from service_config import load_service_config
from storage import SQLStore, messages

METRICS = ['faithfulness','answer_relevancy','context_precision','context_recall']

def identity(sample, profile):
    return hashlib.sha256(json.dumps({'sample':sample,'profile':profile},ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def score_sample(sample, profile, store, scorer):
    key=identity(sample,profile); cached=store.evaluation(key)
    if cached: return cached['scores'], True
    scores=scorer(sample)
    if set(scores)!=set(METRICS) or any(value is None or not math.isfinite(value) for value in scores.values()):
        raise ValueError('Evaluation returned incomplete/non-finite metrics')
    store.save_evaluation(key,sample,profile,scores)
    return scores, False

def historical_sample(store, chat_id, reference):
    with store.engine.connect() as db:
        rows=list(db.execute(select(messages).where(messages.c.chat_id==chat_id).order_by(messages.c.sequence)).mappings())
    for row in reversed(rows):
        if row['role']=='assistant' and row['status']=='complete' and row['payload'].get('evaluation_sample'):
            sample=dict(row['payload']['evaluation_sample'])
            sample['response']=row['content'].split('\n\n文档来源：')[0]
            sample['reference']=reference
            return sample
    raise ValueError('No completed answer with captured retrieval context in this chat')

def make_scorer(config,profile):
    from ragas import evaluate, EvaluationDataset, SingleTurnSample
    from ragas.metrics import Faithfulness, ResponseRelevancy, LLMContextPrecisionWithReference, LLMContextRecall
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.run_config import RunConfig
    from ragas.cache import DiskCacheBackend
    from rag_modules.model_clients import create_llm,create_embeddings
    from langchain_core.prompt_values import StringPromptValue
    def instance_prompt(prompt):
        instruction=('You are evaluating a RAG answer. Read the supplied Input and examples and perform the requested evaluation. '
                     'Return only a JSON INSTANCE with actual filled values matching the supplied output schema. '
                     'Never return the schema itself, $defs, properties, required or type declarations. '
                     '根据输入内容完成评估，输出填好值的 JSON 对象，不要复制 JSON Schema。\n\n')
        return StringPromptValue(text=instruction+prompt.to_string())
    namespace=hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest()[:16]
    cache=DiskCacheBackend(cache_dir=str(ROOT/'runtime/ragas-cache'/namespace))
    judge=create_llm(config)
    if hasattr(judge,'format'): judge.format='json'
    llm=LangchainLLMWrapper(judge,cache=cache)
    generate_sync,generate_async=llm.generate_text,llm.agenerate_text
    def generate(prompt,*args,**kwargs): return generate_sync(instance_prompt(prompt),*args,**kwargs)
    async def agenerate(prompt,*args,**kwargs): return await generate_async(instance_prompt(prompt),*args,**kwargs)
    llm.generate_text,llm.agenerate_text=generate,agenerate
    embeddings=LangchainEmbeddingsWrapper(create_embeddings(config),cache=cache)
    def scorer(sample):
        result=evaluate(EvaluationDataset(samples=[SingleTurnSample(**sample)]),
                        metrics=[Faithfulness(),ResponseRelevancy(strictness=1),LLMContextPrecisionWithReference(),LLMContextRecall()],
                        llm=llm,embeddings=embeddings,raise_exceptions=True,
                        run_config=RunConfig(timeout=180,max_retries=1,max_workers=1),show_progress=True)
        row=result.scores[0]
        return dict(zip(METRICS,[float(row[k]) for k in ('faithfulness','answer_relevancy','llm_context_precision_with_reference','context_recall')]))
    return scorer

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--samples',type=Path,default=ROOT/'evaluation/answer_samples.json')
    parser.add_argument('--chat-id')
    parser.add_argument('--reference',help='Human reference answer for the latest completed historical answer')
    parser.add_argument('--output',type=Path,default=ROOT/'evaluation/answer-results.json')
    parser.add_argument('--capture',action='store_true',help='Capture new real answers before evaluation; otherwise replay stored samples')
    parser.add_argument('--limit',type=int,default=3)
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.limit < 1: parser.error('--limit must be positive')
    if args.chat_id and args.capture: parser.error('--chat-id replays existing answers; do not combine with --capture')
    settings=load_service_config(); store=SQLStore(settings.history_db_path,settings.database_url); store.initialize()
    profile={'ragas':'0.3.9','judge':DEFAULT_CONFIG.llm_model,'endpoint':DEFAULT_CONFIG.llm_base_url,
             'embedding':DEFAULT_CONFIG.embedding_model,'embedding_endpoint':DEFAULT_CONFIG.embedding_base_url,
             'temperature':DEFAULT_CONFIG.temperature,'max_tokens':DEFAULT_CONFIG.max_tokens,'num_ctx':DEFAULT_CONFIG.ollama_num_ctx,
             'provider':DEFAULT_CONFIG.llm_provider,'metrics':METRICS,'strictness':1,'judge_prompt':'json_instance_v2','schema':2}
    try:
        if args.chat_id:
            if not args.reference: parser.error('--chat-id requires --reference for precision/recall evaluation')
            samples=[historical_sample(store,args.chat_id,args.reference)]
        else:
            all_samples=json.loads(args.samples.read_text(encoding='utf-8')); samples=all_samples[:args.limit]
        if args.capture:
            from main import RecipeRAGSystem
            system=RecipeRAGSystem()
            try:
                system.initialize_system(); system.build_knowledge_base()
                for sample in samples:
                    if sample.get('response') and sample.get('retrieved_contexts'):
                        print('Reused captured answer:',sample['user_input'],flush=True)
                        continue
                    info={}; answer,_=system.ask_question_with_routing(sample['user_input'],result_info=info)
                    sample.update(info['evaluation_sample'],response=answer.split('\n\n文档来源：')[0])
                    print('Captured:',sample['user_input'],flush=True)
            finally: system.close()
            args.samples.write_text(json.dumps(all_samples,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        rows=[]; started=time.perf_counter()
        for sample in samples:
            if not sample.get('response') or not sample.get('retrieved_contexts') or not sample.get('reference'):
                raise ValueError('Replay requires response, retrieved_contexts and human reference; use --capture first')
            if store.evaluation(identity(sample,profile)): scores,hit=score_sample(sample,profile,store,None)
            else:
                # Ragas opens an event loop per evaluation; Ollama clients must
                # belong to that evaluation rather than a previously closed loop.
                scorer=make_scorer(DEFAULT_CONFIG,profile)
                scores,hit=score_sample(sample,profile,store,scorer)
            rows.append({'question':sample['user_input'],'scores':scores,'cache_hit':hit})
            print(rows[-1],flush=True)
        report={'profile':profile,'samples':len(rows),'cache_hits':sum(r['cache_hit'] for r in rows),
                'elapsed_seconds':round(time.perf_counter()-started,3),'summary':{k:sum(r['scores'][k] for r in rows)/len(rows) for k in METRICS},'results':rows}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(json.dumps(report['summary'],ensure_ascii=False))
    finally: store.close()

if __name__=='__main__': main()
