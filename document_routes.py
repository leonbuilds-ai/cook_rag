"""Bounded UTF-8 recipe uploads; deliberately excludes PDF/OCR/multimodal parsers."""
import re
from pathlib import Path
from uuid import UUID
from fastapi import Request, Response, Header, UploadFile, File, Form

def register_document_routes(app, settings, authenticate, owner, Error):
    store, redis = app.state.chat_store, app.state.redis

    @app.get('/documents')
    def documents(authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return {'documents':store.catalog()}

    @app.get('/jobs')
    def jobs(request: Request, response: Response, authorization: str | None = Header(default=None)):
        authenticate(authorization)
        return {'jobs':store.job_list(owner(request,response))}

    @app.get('/jobs/{job_id}')
    def job(job_id:UUID, request:Request, authorization:str | None=Header(default=None)):
        authenticate(authorization)
        record = store.job(owner(request),str(job_id))
        if not record: raise Error(404, 'job_not_found','找不到这项任务。')
        return record

    @app.post('/documents/upload', status_code=202)
    async def upload(request:Request, response:Response, file:UploadFile=File(...), category:str=Form('其他'),
                     authorization:str | None=Header(default=None)):
        authenticate(authorization)
        if not app.state.ready: raise Error(503,'not_ready','请等待食谱助手就绪。')
        if redis.ping() != 'ok': raise Error(503,'queue_unavailable','入库队列暂不可用，请稍后重试。')
        if Path(file.filename or '').suffix.lower() not in {'.md','.txt'}:
            raise Error(422,'unsupported_format','仅支持 UTF-8 Markdown 或 TXT 食谱。')
        parts, size = [], 0
        try:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > settings.upload_max_bytes: raise Error(413,'file_too_large','文件不能超过 50 MiB。')
                parts.append(chunk)
        finally: await file.close()
        try: content=b''.join(parts).decode('utf-8-sig')
        except UnicodeDecodeError: raise Error(422,'invalid_encoding','请使用 UTF-8 编码保存食谱。') from None
        if '\x00' in content or not content.strip(): raise Error(422,'invalid_document','食谱不能为空或包含二进制内容。')
        headings = re.findall(r'(?m)^#{1,3}\s+(.+)$',content)
        if not headings or not any(word in content for word in ('食材','原料')) or not any(word in content for word in ('步骤','操作')):
            raise Error(422,'invalid_recipe','请提供含标题、食材或原料、步骤或操作章节的食谱。')
        title=re.sub(r'的做法$','',headings[0]).strip()[:100]
        categories={'荤菜','素菜','汤品','甜品','早餐','主食','水产','调料','饮品','半成品','其他'}
        if category not in categories: raise Error(422,'invalid_category','请选择有效分类。')
        record=store.submit(owner(request,response),title,content,category)
        if record.get('id'):
            try: redis.enqueue(record['id'])
            except Exception:
                # Ledger remains queued: startup recovery re-dispatches without losing the upload.
                raise Error(503,'queue_unavailable','食谱已保存，队列暂不可用；服务恢复后将继续入库。') from None
        return record

    @app.post('/jobs/{job_id}/retry', status_code=202)
    def retry(job_id:UUID, request:Request, authorization:str | None=Header(default=None)):
        authenticate(authorization)
        record=store.job(owner(request),str(job_id))
        if not record: raise Error(404,'job_not_found','找不到这项任务。')
        if record['status']!='failed': raise Error(409,'job_not_failed','只有失败任务需要重试。')
        if redis.ping()!='ok': raise Error(503,'queue_unavailable','入库队列暂不可用。')
        store.job_progress(str(job_id),'等待重试',0,'queued'); redis.enqueue(str(job_id))
        return store.job(owner(request),str(job_id))
