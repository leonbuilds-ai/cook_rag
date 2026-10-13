"""Real multilingual Cross-Encoder scoring after hybrid RRF; never bypass filters."""
import logging
import math
import threading
import time
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

class CrossEncoderReranker:
    def __init__(self, config, model=None):
        self.config, self.model = config, model
        self.lock = threading.Lock()
        self.status = 'disabled' if not config.rerank_enabled else 'not_loaded'

    def load(self):
        if self.model is None:
            from sentence_transformers import CrossEncoder
            import torch
            torch.set_num_threads(self.config.rerank_threads)
            self.model = CrossEncoder(self.config.rerank_model, device='cpu', max_length=512,
                                      trust_remote_code=False, model_kwargs={'use_safetensors':True})
        self.status = 'ready'

    def rerank(self, query, docs, top_k):
        unique = list({d.metadata['parent_id']: d for d in docs}.values())
        trace = {'enabled':self.config.rerank_enabled, 'model':self.config.rerank_model, 'candidates':len(unique)}
        if not self.config.rerank_enabled or len(unique) < 2:
            return unique[:top_k], {**trace, 'applied':False, 'status':'disabled' if not self.config.rerank_enabled else 'single_candidate'}
        started = time.perf_counter()
        try:
            with self.lock:
                self.load()
                scores = self.model.predict([(query, d.metadata.get('dish_name','')+'\n'+d.page_content[:6000]) for d in unique],
                                            batch_size=4, show_progress_bar=False)
            scores = [float(score) for score in scores]
            if len(scores) != len(unique) or not all(math.isfinite(s) for s in scores): raise ValueError('Invalid reranker scores')
            order = sorted(range(len(unique)), key=lambda i:scores[i], reverse=True)
            ranked = [Document(page_content=unique[i].page_content, metadata={**unique[i].metadata, 'rerank_score':scores[i]}) for i in order[:top_k]]
            return ranked, {**trace, 'applied':True, 'status':'ready', 'latency_ms':round((time.perf_counter()-started)*1000,2)}
        except Exception as exc:
            self.status = 'unavailable'
            logger.warning('Cross-Encoder unavailable; using RRF', extra={'error_type':type(exc).__name__})
            return unique[:top_k], {**trace, 'applied':False, 'status':'fallback_rrf'}
